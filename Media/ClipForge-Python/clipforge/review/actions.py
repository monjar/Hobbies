"""Review decisions. The web queue and the CLI both go through these functions."""

import time

from ..db import DB
from ..errors import ClipforgeError


def _record(db: DB, job_id: str, action: str, reason: str | None) -> None:
    db.insert("reviews", job_id=job_id, action=action, reason=reason, created_at=time.time())


def approve(db: DB, job_id: str) -> None:
    job = db.job(job_id)
    if not job["live"]:
        raise ClipforgeError(f"job {job_id} was produced with mock providers and cannot be approved")
    db.transition(job_id, "approved")
    _record(db, job_id, "approve", None)


def reject(db: DB, job_id: str, reason: str) -> None:
    """Reject a pending render. The reason is required and later fed to the script prompt."""
    reason = (reason or "").strip()
    if not reason:
        raise ClipforgeError("a rejection needs a reason")
    db.transition(job_id, "rejected")
    _record(db, job_id, "reject", reason)
