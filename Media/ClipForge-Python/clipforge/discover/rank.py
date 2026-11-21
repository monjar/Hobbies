"""Store discovered videos as topics and rank them. Ranking reads only stored data."""

import re
import time
import uuid
from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..db import DB
from ..log import log
from .youtube import LICENCE_CC, VideoMeta, YouTubeClient

# Title wording -> narration angle. Only angles present in config/style.yaml are suggested.
ANGLE_HINTS = [
    (re.compile(r"\b(vs\.?|versus|compar\w*|gta ?v|gta ?5)\b", re.I), "gta-v-comparison"),
    (re.compile(r"\b(details?|easter eggs?|hidden|secrets?|noticed?)\b", re.I), "detail-spotting"),
    (re.compile(r"\b(how|explain\w*|mechanics?|system|works?|guide)\b", re.I), "mechanic-explained"),
]
DEFAULT_ANGLE = "what-you-missed"


def views_per_hour(views: int, published_at: datetime, fetched_at: datetime) -> float:
    hours = max((fetched_at - published_at).total_seconds() / 3600, 1.0)  # under an hour counts as one
    return views / hours


def suggest_angle(title: str, available: list[str]) -> str:
    for pattern, angle in ANGLE_HINTS:
        if angle in available and pattern.search(title):
            return angle
    return DEFAULT_ANGLE if DEFAULT_ANGLE in available else available[0]


def store_topics(db: DB, videos: list[VideoMeta], keyword: str, fetched_at: datetime) -> None:
    for video in videos:
        db.conn.execute(
            "INSERT OR REPLACE INTO topics (video_id, title, channel_id, channel_title, published_at, duration_s, "
            "views, likes, licence, keyword, fetched_at, views_per_hour) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video.id, video.title, video.channel_id, video.channel_title, video.published_at.isoformat(),
                video.duration_s, video.views, video.likes, video.licence, keyword, fetched_at.isoformat(),
                views_per_hour(video.views, video.published_at, fetched_at),
            ),
        )


def discover(db: DB, settings: Settings, make_client) -> dict:
    """Search each configured keyword and store the results. Returns the run's quota usage.

    `make_client(on_call)` builds the YouTubeClient, so quota is logged for every request.
    """
    cfg = settings.sources.discovery
    run_id = uuid.uuid4().hex[:12]

    def on_call(method: str, bucket: str, units: int) -> None:
        db.insert("quota_log", run_id=run_id, api="youtube", method=method, bucket=bucket, units=units, created_at=time.time())

    client: YouTubeClient = make_client(on_call)
    now = datetime.now(UTC)
    stored = 0
    since = now - timedelta(days=cfg.lookback_days)
    for keyword in cfg.keywords:
        ids = client.search(keyword, published_after=since, max_results=cfg.max_results)
        if cfg.creative_commons_search:
            # Trending results are almost all standard licence, so downloadable footage needs its own search.
            extra = client.search(keyword, published_after=since, licence=LICENCE_CC, max_results=cfg.max_results)
            ids += [video_id for video_id in extra if video_id not in ids]
        videos = client.videos(ids) if ids else []
        store_topics(db, videos, keyword, now)
        stored += len(videos)
    usage = {
        row["bucket"]: row["units"]
        for row in db.all("SELECT bucket, SUM(units) AS units FROM quota_log WHERE run_id = ? GROUP BY bucket", run_id)
    }
    log("discover_done", run_id=run_id, topics=stored, quota=usage)
    return {"run_id": run_id, "topics": stored, "quota": usage}


def ranked_topics(db: DB, limit: int = 20, creative_commons_only: bool = False) -> list[dict]:
    """Fastest-growing videos first. Deterministic for a given database."""
    where = "WHERE licence = 'creativeCommon'" if creative_commons_only else ""
    rows = db.all(f"SELECT * FROM topics {where} ORDER BY views_per_hour DESC, video_id ASC LIMIT ?", limit)
    return [dict(row) for row in rows]


def topic_context(db: DB, limit: int = 5) -> str:
    """Trending titles, as background for the script prompt. Never a trigger for downloads."""
    return "\n".join(f"- {t['title']} ({t['views_per_hour']:.0f} views/hour)" for t in ranked_topics(db, limit))
