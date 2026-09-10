"""Blood-report analysis service.

Keeps a short per-user context of recently parsed reports so a user can ask
follow-up questions ("what should I do next?") without re-uploading.

The context lives in Postgres rather than process memory: an in-process dict
was lost on every restart and invisible to sibling workers, so a follow-up
would silently answer with no report attached.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from psycopg2.extras import Json

from src.core.config import settings
from src.core.database import get_conn
from src.features.medical_report import parser

logger = logging.getLogger(__name__)

# How many reports to keep per user, and how long a report stays usable as
# follow-up context.
_HISTORY_LIMIT = 5
_HISTORY_TIMEOUT = timedelta(hours=1)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _remember(user_id: str, report_json: dict | None) -> None:
    """Store a freshly parsed report, then prune stale/excess rows for the user."""
    if not report_json:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO medical_report_context
                (id, user_id, structured_report, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), user_id, Json(report_json), datetime.now(timezone.utc)),
        )
        # Drop anything past the TTL, then anything past the per-user cap.
        cur.execute(
            """
            DELETE FROM medical_report_context
            WHERE user_id = %s AND created_at < %s
            """,
            (user_id, datetime.now(timezone.utc) - _HISTORY_TIMEOUT),
        )
        cur.execute(
            """
            DELETE FROM medical_report_context
            WHERE user_id = %s AND id NOT IN (
                SELECT id FROM medical_report_context
                WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
            )
            """,
            (user_id, user_id, _HISTORY_LIMIT),
        )


def _last_report(user_id: str) -> dict | None:
    """The most recent report still inside the TTL window, if any."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT structured_report FROM medical_report_context
            WHERE user_id = %s AND created_at >= %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, datetime.now(timezone.utc) - _HISTORY_TIMEOUT),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", text, flags=re.DOTALL)
    return text.strip()


def _build_prompt(query: str, report_json: dict | None) -> str:
    prompt = f"""
Analyze the blood test results and answer the question using these rules:
1. Keep answers concise (100-150 words) and easy to understand.
2. Use simple analogies to explain medical concepts.
3. For abnormal values, explain meaning, possible causes and reasonable next steps.
4. Indicate urgency (routine / moderate / immediate attention) for concerning values.
5. Always note this is informational, not a diagnosis.
6. Structure: Summary of findings, Explanation of key values, Recommendations.

Current Query: {query}
"""
    if report_json:
        info = report_json.get("patient_info", {})
        prompt += f"""
Patient Age: {info.get('age', 'Unknown')}
Patient Gender: {info.get('gender', 'Unknown')}
Blood Test Results (JSON):
{json.dumps(report_json, indent=2)}
"""
    else:
        prompt += "\nNo blood test results available."
    return prompt


async def analyze(user_id: str, query: str | None, file_path: str | None) -> dict:
    report_json: dict | None = None

    if file_path:
        report_text = await parser.parse_blood_report(file_path)
        try:
            report_json = await parser.structure_report(report_text)
            if not isinstance(report_json, dict):
                report_json = None
        except Exception as exc:  # noqa: BLE001
            logger.error("structure_report failed: %s", exc)
            report_json = None
        effective_query = query.strip() if query else "Explain my blood test results"
    else:
        report_json = _last_report(user_id)
        effective_query = (query or "").strip()

    prompt = _build_prompt(effective_query, report_json)
    completion = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert medical professional who explains blood test "
                    "results in simple terms."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    response = _clean(completion.choices[0].message.content or "")
    if not response:
        raise ValueError("Empty response from model")

    _remember(user_id, report_json)
    return {"structured_report": report_json, "response": response}
