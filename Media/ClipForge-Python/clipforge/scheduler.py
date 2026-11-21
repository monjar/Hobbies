"""Unattended batch: discover -> ingest -> make, stopping at pending_review. Never publishes."""

from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .db import DB
from .discover.rank import suggest_angle
from .errors import ClipforgeError
from .log import log
from .pipeline import WINDOW_MARGIN_S, Pipeline

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MAX_TOPIC_S = 3600  # longer uploads are full playthroughs: huge downloads and spoiler-heavy


def _attempted(db: DB, path: Path) -> bool:
    # Any earlier job counts, failed ones too: an unattended run must not pay again and again
    # for footage that already failed. Retry those by hand with `clipforge make`.
    return db.one("SELECT 1 FROM jobs WHERE source_path = ? LIMIT 1", str(path)) is not None


def worklist(db: DB, settings: Settings):
    """Yield (kind, target, angle): unprocessed inbox files first, then downloadable topics by rank."""
    angles = list(settings.style.angles)
    if settings.inbox.is_dir():
        for path in sorted(settings.inbox.iterdir()):
            if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file() and not _attempted(db, path.resolve()):
                yield "file", path.resolve(), angles[0]
    allow = settings.sources.allowlist_channels
    min_s = settings.style.output.min_s + WINDOW_MARGIN_S
    for topic in db.all("SELECT * FROM topics ORDER BY views_per_hour DESC, video_id ASC"):
        allowed = topic["channel_id"] in allow or (
            settings.sources.allow_creative_commons and topic["licence"] == "creativeCommon"
        )
        if not allowed or not min_s <= topic["duration_s"] <= MAX_TOPIC_S:
            continue
        url = f"https://www.youtube.com/watch?v={topic['video_id']}"
        source = db.one("SELECT path FROM sources WHERE kind = 'url' AND url = ?", url)
        if source and _attempted(db, Path(source["path"])):
            continue
        if db.one("SELECT 1 FROM ingest_failures WHERE url = ?", url):
            continue
        yield "url", url, suggest_angle(topic["title"], angles)


def run_batch(
    db: DB,
    settings: Settings,
    pipeline: Pipeline,
    jobs: int,
    live: bool,
    discover: Callable[[], object] | None = None,
    ingest: Callable[[str], Path] | None = None,
    notify: Callable[[str], None] | None = None,
) -> dict:
    """Make up to `jobs` videos. Each lands in pending_review; failures are logged and skipped."""
    if discover:
        try:
            discover()
        except ClipforgeError as exc:
            log("run_discover_failed", level="error", reason=str(exc))

    made, failed = [], []
    for kind, target, angle in worklist(db, settings):
        if len(made) >= jobs:
            break
        try:
            if kind == "url":
                if ingest is None:
                    continue
                try:
                    path = ingest(target)
                except ClipforgeError as exc:
                    db.conn.execute("INSERT OR REPLACE INTO ingest_failures (url, reason) VALUES (?, ?)", (target, str(exc)))
                    raise
            else:
                path = target
            made.append(pipeline.make(path, angle, live))
        except ClipforgeError as exc:
            failed.append({"target": str(target), "reason": str(exc)})
            log("run_item_failed", level="error", target=str(target), reason=str(exc))

    pending = db.one("SELECT COUNT(*) FROM jobs WHERE state = 'pending_review'")[0]
    if notify and pending:
        try:
            notify(f"ClipForge: {pending} video{'s' if pending != 1 else ''} waiting for review")
        except ClipforgeError as exc:
            log("run_notify_failed", level="error", reason=str(exc))
    summary = {"made": made, "failed": failed, "pending_review": pending}
    log("run_done", made=len(made), failed=len(failed), pending_review=pending)
    return summary
