"""YouTube Data API v3 client: search and video metadata. API shape per docs/integrations.md.

This is discovery metadata only. Nothing here downloads video.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..errors import ProviderError

BASE_URL = "https://www.googleapis.com/youtube/v3"
IDS_PER_CALL = 50
LICENCE_CC = "creativeCommon"
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass(frozen=True)
class VideoMeta:
    id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration_s: int
    views: int
    likes: int
    licence: str  # "youtube" or "creativeCommon", as reported by the API

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def parse_duration(value: str) -> int:
    """ISO 8601 duration (PT1H2M3S) to seconds."""
    match = _DURATION.fullmatch(value or "")
    if not match or not any(match.groups()):
        return 0
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def video_id_from_url(url: str) -> str | None:
    """Video ID from a youtube.com / youtu.be URL; None for anything else."""
    try:
        parsed = httpx.URL(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.host.lower().removeprefix("www.").removeprefix("m.")
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/")
    elif host == "youtube.com":
        if parsed.path == "/watch":
            candidate = parsed.params.get("v")
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            candidate = parsed.path.split("/")[2]
    return candidate if candidate and _VIDEO_ID.fullmatch(candidate) else None


class YouTubeClient:
    """`on_call(method, bucket, units)` is invoked once per HTTP request, for quota logging."""

    def __init__(
        self,
        api_key: str,
        on_call: Callable[[str, str, int], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._key = api_key
        self._on_call = on_call or (lambda method, bucket, units: None)
        self._client = httpx.Client(base_url=BASE_URL, timeout=30.0, transport=transport)

    def _get(self, method: str, bucket: str, path: str, params: dict) -> dict:
        self._on_call(method, bucket, 1)  # every request costs quota, including failed ones
        try:
            response = self._client.get(path, params={**params, "key": self._key})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                message = exc.response.json()["error"]["message"]
            except Exception:
                message = exc.response.text[:200]
            # The request URL carries the key, so it is never included in the error.
            raise ProviderError(f"youtube {method} failed ({exc.response.status_code}): {message}") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"youtube {method} connection error: {type(exc).__name__}") from None
        return response.json()

    def search(
        self,
        query: str,
        *,
        published_after: datetime | None = None,
        licence: str = "any",
        order: str = "viewCount",
        max_results: int = 25,
    ) -> list[str]:
        """Video IDs matching a query. One call in the search bucket (100 calls a day by default)."""
        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "order": order,
            "videoLicense": licence,
            "maxResults": max(1, min(max_results, 50)),
        }
        if published_after:
            params["publishedAfter"] = published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get("search.list", "search", "/search", params)
        return [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]

    def videos(self, ids: list[str]) -> list[VideoMeta]:
        """Metadata, statistics and licence for each ID. One quota unit per 50 IDs."""
        found = []
        for offset in range(0, len(ids), IDS_PER_CALL):
            batch = ids[offset : offset + IDS_PER_CALL]
            data = self._get(
                "videos.list", "general", "/videos",
                {"part": "snippet,contentDetails,statistics,status", "id": ",".join(batch)},
            )
            for item in data.get("items", []):
                snippet, stats = item["snippet"], item.get("statistics", {})
                found.append(
                    VideoMeta(
                        id=item["id"],
                        title=snippet["title"],
                        channel_id=snippet["channelId"],
                        channel_title=snippet.get("channelTitle", ""),
                        published_at=datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00")),
                        duration_s=parse_duration(item.get("contentDetails", {}).get("duration", "")),
                        views=int(stats.get("viewCount", 0)),
                        likes=int(stats.get("likeCount", 0)),
                        licence=item.get("status", {}).get("license", "youtube"),
                    )
                )
        return found

    def video(self, video_id: str) -> VideoMeta | None:
        found = self.videos([video_id])
        return found[0] if found else None
