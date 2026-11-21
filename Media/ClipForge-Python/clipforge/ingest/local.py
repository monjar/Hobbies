"""Ingest of footage from the local inbox."""

import hashlib
import time
from pathlib import Path

from ..config import Settings
from ..db import DB
from ..errors import MediaError
from ..media import probe
from .policy import check_local


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_local(db: DB, settings: Settings, path: Path, sha256: str) -> int:
    """Record a local source. Re-checks policy so no caller can skip it."""
    decision = check_local(path, settings).require()
    info = probe(path)
    if not info.has_video:
        raise MediaError(f"{path} has no video stream")
    existing = db.one("SELECT id FROM sources WHERE sha256 = ? AND kind = 'local'", sha256)
    if existing:
        return existing["id"]
    return db.insert(
        "sources",
        kind="local",
        path=str(path),
        policy_rule=decision.rule,
        sha256=sha256,
        duration_s=info.duration,
        ingested_at=time.time(),
    )
