"""
V1 Session Database: SQLite + WAL mode for session persistence.

Provides durable storage for session metadata, project states, and user
profiles so that sessions survive process restarts.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_VERSION = 1

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    metadata     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_states (
    session_id   TEXT PRIMARY KEY,
    state_json   TEXT NOT NULL,
    updated_at   REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
"""


class SessionDB:
    """
    SQLite-backed session persistence with WAL mode for concurrent reads.

    Usage::

        db = SessionDB(".storyline/sessions.db")
        db.create_session("sess-1", "proj-1")
        db.save_project_state("sess-1", project_state.model_dump_json())
        state_json = db.load_project_state("sess-1")
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ── connection management ────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_INIT_SQL)

        # Check / set schema version
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
        conn.commit()
        logger.info(f"[SessionDB] Initialised at {self.db_path}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── session CRUD ─────────────────────────────────────────────

    def create_session(
        self,
        session_id: str,
        project_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create a new session record (idempotent — upserts on conflict)."""
        now = time.time()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO sessions (session_id, project_id, created_at, updated_at, status, metadata)
            VALUES (?, ?, ?, ?, 'active', ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                metadata   = excluded.metadata
            """,
            (session_id, project_id, now, now, meta_json),
        )
        conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a session by ID."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def list_sessions(
        self, status: str = "active", limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List sessions ordered by most-recently-updated first."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
            (status, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def update_session_status(self, session_id: str, status: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, time.time(), session_id),
        )
        conn.commit()

    # ── project state persistence ────────────────────────────────

    def save_project_state(self, session_id: str, state_json: str) -> None:
        """Persist project state JSON for a session."""
        now = time.time()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO project_states (session_id, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (session_id, state_json, now),
        )
        conn.commit()

    def load_project_state(self, session_id: str) -> Optional[str]:
        """Load project state JSON for a session (or None)."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT state_json FROM project_states WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        return row["state_json"] if row else None

    # ── user profile persistence ─────────────────────────────────

    def save_user_profile(self, user_id: str, profile_json: str) -> None:
        now = time.time()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, profile_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at   = excluded.updated_at
            """,
            (user_id, profile_json, now),
        )
        conn.commit()

    def load_user_profile(self, user_id: str) -> Optional[str]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT profile_json FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        return row["profile_json"] if row else None
