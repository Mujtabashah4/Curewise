"""Database schema bootstrap.

Only user-facing tables survive the redesign: ``users``, ``medical_history``,
``general_chat_history`` and ``medical_report_context``. All hospital-management
tables (hospitals, departments, doctors, availability, appointments, admins)
are gone.
"""

import logging

from src.core.database import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    profile_picture TEXT,
    date_of_birth DATE,
    gender TEXT,
    address TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medical_history (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conditions TEXT,
    allergies TEXT,
    notes TEXT,
    updated_at TIMESTAMP,
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS general_chat_history (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    query TEXT,
    response TEXT,
    created_at TIMESTAMP
);

-- The last few parsed blood reports per user, so follow-up questions work
-- without re-uploading. Lives in Postgres rather than process memory so it
-- survives a restart and is shared across workers. Rows are pruned on write.
CREATE TABLE IF NOT EXISTS medical_report_context (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    structured_report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS medical_report_context_user_created_idx
    ON medical_report_context (user_id, created_at DESC);
"""


def init_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_SCHEMA)
        cur.close()
    logger.info("Database schema initialized")
