#!/usr/bin/env python
"""Seed the Pinecone knowledge base that the chat assistant retrieves from.

``src/features/chat/rag.py`` will happily create an *empty* index at startup —
retrieval then returns nothing and every answer degrades to "I don't have that
in my knowledge base". This script is the missing half: it loads the curated
Q&A corpus in ``data/rag/data.csv``, chunks it, embeds it and upserts it into
the same index the app reads.

Index name, embedding model and dimension are imported from the app itself so
the two can never drift apart.

Usage
-----
    cd backend
    python scripts/ingest_rag.py                  # full ingest
    python scripts/ingest_rag.py --limit 200      # quick smoke test
    python scripts/ingest_rag.py --dry-run        # chunk + count, no API calls
    python scripts/ingest_rag.py --recreate       # drop the index first

Vector ids are deterministic (``<row>_<chunk>``), so re-running overwrites
rather than duplicating.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

# Allow both `python scripts/ingest_rag.py` (from backend/) and
# `python backend/scripts/ingest_rag.py` (from the repo root).
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402
from openai import OpenAI  # noqa: E402
from pinecone import Pinecone, ServerlessSpec  # noqa: E402

from src.core.config import DATA_DIR, settings  # noqa: E402
from src.features.chat.rag import (  # noqa: E402
    EMBED_MODEL,
    EMBEDDING_DIMENSION,
    PINECONE_INDEX_NAME,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("ingest_rag")

DEFAULT_CSV = DATA_DIR / "rag" / "data.csv"
REQUIRED_COLUMNS = ("qtype", "Question", "Answer")

# 4000 chars keeps each vector's metadata well under Pinecone's 40 KB cap.
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 200
EMBED_BATCH = 128   # texts per OpenAI embeddings call
UPSERT_BATCH = 100  # vectors per Pinecone upsert call

MAX_RETRIES = 5


def _retry(fn, *, what: str):
    """Call ``fn`` with exponential backoff on transient provider errors."""
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - provider errors vary widely
            if attempt == MAX_RETRIES:
                raise
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.0fs",
                what, attempt, MAX_RETRIES, exc, delay,
            )
            time.sleep(delay)
            delay *= 2


def load_rows(csv_path: Path, limit: int | None) -> list[dict]:
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                f"{csv_path} is missing required column(s): {', '.join(missing)}"
            )
        rows = list(reader)

    if limit is not None:
        rows = rows[:limit]
    logger.info("Loaded %d rows from %s", len(rows), csv_path)
    return rows


def build_chunks(rows: list[dict]) -> list[tuple[str, str, dict]]:
    """Return ``(vector_id, text, metadata)`` for every chunk of every row."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    out: list[tuple[str, str, dict]] = []
    skipped = 0

    for i, row in enumerate(rows):
        question = (row.get("Question") or "").strip()
        answer = (row.get("Answer") or "").strip()
        if not question or not answer:
            skipped += 1
            continue

        combined = f"Q: {question}\nA: {answer}"
        for j, chunk in enumerate(splitter.split_text(combined)):
            out.append(
                (
                    f"{i}_{j}",
                    chunk,
                    # "text" is the key LangChain's PineconeVectorStore reads by
                    # default — the app relies on it. Don't rename it.
                    {"qtype": (row.get("qtype") or "").strip(), "text": chunk},
                )
            )

    if skipped:
        logger.warning("Skipped %d row(s) with an empty question or answer", skipped)
    logger.info("Built %d chunks from %d rows", len(out), len(rows) - skipped)
    return out


def ensure_index(pc: Pinecone, recreate: bool) -> None:
    existing = pc.list_indexes().names()

    if recreate and PINECONE_INDEX_NAME in existing:
        logger.warning("Deleting existing index '%s'", PINECONE_INDEX_NAME)
        pc.delete_index(PINECONE_INDEX_NAME)
        existing = pc.list_indexes().names()

    if PINECONE_INDEX_NAME in existing:
        logger.info("Using existing index '%s'", PINECONE_INDEX_NAME)
        return

    logger.info(
        "Creating index '%s' (dim=%d, cosine)", PINECONE_INDEX_NAME, EMBEDDING_DIMENSION
    )
    pc.create_index(
        name=PINECONE_INDEX_NAME,
        dimension=EMBEDDING_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    while not pc.describe_index(PINECONE_INDEX_NAME).status["ready"]:
        logger.info("Waiting for index to become ready…")
        time.sleep(5)
    logger.info("Index ready")


def ingest(chunks: list[tuple[str, str, dict]], recreate: bool) -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set (check backend/.env)")
    if not settings.PINECONE_API_KEY:
        raise SystemExit("PINECONE_API_KEY is not set (check backend/.env)")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    ensure_index(pc, recreate)
    index = pc.Index(PINECONE_INDEX_NAME)

    total = len(chunks)
    done = 0
    pending: list[tuple[str, list[float], dict]] = []

    for start in range(0, total, EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        texts = [text for _, text, _ in batch]

        # Bind the loop variable as a default arg: the lambda outlives the
        # statement only inside _retry, but this keeps it unambiguous.
        response = _retry(
            lambda t=texts: client.embeddings.create(model=EMBED_MODEL, input=t),
            what="Embedding batch",
        )
        embeddings = [d.embedding for d in response.data]

        pending.extend(
            (vid, emb, meta)
            for (vid, _, meta), emb in zip(batch, embeddings)
        )

        while len(pending) >= UPSERT_BATCH:
            head, pending = pending[:UPSERT_BATCH], pending[UPSERT_BATCH:]
            _retry(lambda h=head: index.upsert(vectors=h), what="Upsert batch")

        done += len(batch)
        logger.info("Embedded %d/%d chunks (%.0f%%)", done, total, 100 * done / total)

    if pending:
        _retry(lambda v=pending: index.upsert(vectors=v), what="Final upsert")

    stats = index.describe_index_stats()
    logger.info("Done. Index now holds %s vectors", stats.get("total_vector_count"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="source CSV")
    parser.add_argument("--limit", type=int, help="only ingest the first N rows")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="chunk and report counts without calling OpenAI or Pinecone",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="delete and recreate the index before ingesting",
    )
    args = parser.parse_args()

    rows = load_rows(args.csv, args.limit)
    chunks = build_chunks(rows)
    if not chunks:
        raise SystemExit("Nothing to ingest.")

    if args.dry_run:
        chars = sum(len(t) for _, t, _ in chunks)
        logger.info(
            "Dry run: %d chunks, %d characters, ~%d tokens (rough) — no API calls made",
            len(chunks), chars, chars // 4,
        )
        return

    ingest(chunks, args.recreate)


if __name__ == "__main__":
    main()
