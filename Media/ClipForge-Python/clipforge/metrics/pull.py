"""Store daily performance snapshots per post: pulled from YouTube Analytics, or entered by hand."""

import time
from collections.abc import Callable
from datetime import date

import httpx

from ..db import DB
from ..errors import ClipforgeError, ProviderError
from ..log import log

ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = ("views", "likes", "shares", "averageViewDuration")
MAX_VIDEOS = 200


def record(db: DB, post_row_id: int, on: date, views: int, likes: int, shares: int, avg_view_s: float | None) -> None:
    db.conn.execute(
        "INSERT OR REPLACE INTO metrics (post_id, pulled_on, views, likes, shares, avg_view_duration_s) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (post_row_id, on.isoformat(), views, likes, shares, avg_view_s),
    )


def pull_youtube(
    db: DB, access_token: Callable[[], str], today: date, transport: httpx.BaseTransport | None = None
) -> int:
    """Fetch lifetime totals for every published YouTube post and store them as today's snapshot."""
    posts = db.all(
        "SELECT id, post_id, created_at FROM posts WHERE platform = 'youtube' AND status = 'published' "
        "ORDER BY created_at DESC LIMIT ?", MAX_VIDEOS,
    )
    if not posts:
        return 0
    first_day = date.fromtimestamp(min(row["created_at"] for row in posts))
    params = {
        "ids": "channel==MINE",
        "startDate": first_day.isoformat(),
        "endDate": today.isoformat(),
        "metrics": ",".join(METRICS),
        "dimensions": "video",
        "filters": "video==" + ",".join(row["post_id"] for row in posts),
        "sort": "-views",
        "maxResults": str(MAX_VIDEOS),
    }
    try:
        response = httpx.Client(timeout=30.0, transport=transport).get(
            ANALYTICS_URL, params=params, headers={"Authorization": f"Bearer {access_token()}"}
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"youtube analytics connection error: {type(exc).__name__}") from exc
    if response.status_code != 200:
        try:
            message = response.json()["error"]["message"]
        except Exception:
            message = response.text[:200]
        raise ProviderError(f"youtube analytics failed ({response.status_code}): {message}")
    data = response.json()
    columns = [header["name"] for header in data.get("columnHeaders", [])]
    by_video = {row[columns.index("video")]: dict(zip(columns, row)) for row in data.get("rows", [])}
    stored = 0
    for post in posts:
        row = by_video.get(post["post_id"])
        if row is None:  # no views yet, or the video was removed
            continue
        record(db, post["id"], today, int(row["views"]), int(row["likes"]), int(row["shares"]),
               float(row["averageViewDuration"]))
        stored += 1
    log("metrics_pulled", platform="youtube", posts=len(posts), stored=stored)
    return stored


def add_manual(
    db: DB, job_id: str, platform: str, on: date, views: int, likes: int, shares: int, avg_view_s: float | None
) -> None:
    """Enter numbers read off a platform's own analytics page, for posts uploaded by hand."""
    post = db.one("SELECT id FROM posts WHERE job_id = ? AND platform = ?", job_id, platform)
    if post is None:
        raise ClipforgeError(f"job {job_id} has no {platform} post; publish or export it first")
    record(db, post["id"], on, views, likes, shares, avg_view_s)
    log("metrics_added", job_id, platform=platform, at=time.time())
