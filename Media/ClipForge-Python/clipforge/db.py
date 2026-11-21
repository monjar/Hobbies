"""SQLite state: schema, migrations, and the job state machine (single source of truth)."""

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .errors import ClipforgeError, IllegalTransition

STATES = (
    "discovered",
    "ingested",
    "analysed",
    "scripted",
    "voiced",
    "rendered",
    "pending_review",
    "approved",
    "published",
    "rejected",
    "failed",
)
TERMINAL = frozenset({"published", "rejected", "failed"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset({"ingested"}),
    "ingested": frozenset({"analysed"}),
    "analysed": frozenset({"scripted"}),
    "scripted": frozenset({"voiced"}),
    "voiced": frozenset({"rendered"}),
    "rendered": frozenset({"pending_review"}),
    "pending_review": frozenset({"approved", "rejected"}),
    "approved": frozenset({"published"}),
}

# One entry per schema version; applied in order, tracked with PRAGMA user_version.
MIGRATIONS = [
    """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        fail_reason TEXT,
        angle TEXT NOT NULL,
        live INTEGER NOT NULL,
        source_path TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        source_id INTEGER REFERENCES sources(id),
        template TEXT NOT NULL,
        caption_style TEXT NOT NULL,
        parent_job_id TEXT REFERENCES jobs(id),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE sources (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        path TEXT,
        url TEXT,
        channel TEXT,
        licence TEXT,
        policy_rule TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        duration_s REAL NOT NULL,
        ingested_at REAL NOT NULL
    );
    CREATE TABLE moments (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        segments TEXT NOT NULL,
        description TEXT NOT NULL,
        transcript TEXT NOT NULL,
        pick_reason TEXT NOT NULL,
        spoiler_label TEXT NOT NULL,
        spoiler_reason TEXT NOT NULL
    );
    CREATE TABLE scripts (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
        body TEXT NOT NULL,
        est_speech_s REAL NOT NULL,
        attempts INTEGER NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE renders (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
        path TEXT NOT NULL,
        sidecar_path TEXT NOT NULL,
        duration_s REAL NOT NULL,
        narration_coverage REAL NOT NULL,
        max_source_segment_s REAL NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE costs (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id),
        provider TEXT NOT NULL,
        task TEXT NOT NULL,
        model TEXT NOT NULL,
        usd REAL NOT NULL,
        detail TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id),
        action TEXT NOT NULL,
        reason TEXT,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE topics (
        video_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        channel_title TEXT NOT NULL,
        published_at TEXT NOT NULL,
        duration_s INTEGER NOT NULL,
        views INTEGER NOT NULL,
        likes INTEGER NOT NULL,
        licence TEXT NOT NULL,
        keyword TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        views_per_hour REAL NOT NULL
    );
    CREATE TABLE quota_log (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        api TEXT NOT NULL,
        method TEXT NOT NULL,
        bucket TEXT NOT NULL,
        units INTEGER NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE keyframe_hashes (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id),
        hash TEXT NOT NULL
    );
    CREATE INDEX keyframe_hashes_job ON keyframe_hashes(job_id);
    """,
]


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The review server touches the DB from worker threads; access is serialised by SQLite.
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            self.conn.executescript(f"BEGIN;{MIGRATIONS[index]}PRAGMA user_version = {index + 1};COMMIT;")

    # -- generic helpers -------------------------------------------------
    def insert(self, table: str, **cols) -> int:
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        cur = self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values()))
        return cur.lastrowid

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    # -- jobs ------------------------------------------------------------
    def create_job(self, **cols) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.insert("jobs", id=job_id, state="discovered", created_at=now, updated_at=now, **cols)
        return job_id

    def job(self, job_id: str) -> sqlite3.Row:
        row = self.one("SELECT * FROM jobs WHERE id = ?", job_id)
        if row is None:
            raise ClipforgeError(f"no such job: {job_id}")
        return row

    def set_job(self, job_id: str, **cols) -> None:
        sets = ", ".join(f"{name} = ?" for name in cols)
        self.conn.execute(f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?", [*cols.values(), time.time(), job_id])

    def transition(self, job_id: str, new_state: str, reason: str | None = None) -> None:
        """The only way a job changes state. Illegal moves raise."""
        current = self.job(job_id)["state"]
        if new_state == "failed":
            if current in TERMINAL:
                raise IllegalTransition(f"job {job_id}: {current} -> failed is not allowed")
            if not reason:
                raise IllegalTransition("a failed job needs a reason")
        elif new_state not in TRANSITIONS.get(current, frozenset()):
            raise IllegalTransition(f"job {job_id}: {current} -> {new_state} is not allowed")
        self.set_job(job_id, state=new_state, fail_reason=reason if new_state == "failed" else None)

    # -- costs -----------------------------------------------------------
    def add_cost(self, job_id: str, provider: str, task: str, model: str, usd: float, detail: dict) -> None:
        self.insert(
            "costs",
            job_id=job_id,
            provider=provider,
            task=task,
            model=model,
            usd=usd,
            detail=json.dumps(detail),
            created_at=time.time(),
        )

    def total_cost(self, job_id: str) -> float:
        return self.one("SELECT COALESCE(SUM(usd), 0) FROM costs WHERE job_id = ?", job_id)[0]
