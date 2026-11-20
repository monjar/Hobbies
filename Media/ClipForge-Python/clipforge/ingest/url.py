"""Gated URL ingest. The policy decision comes first; a denied URL never reaches the downloader."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..db import DB
from ..discover.youtube import VideoMeta
from ..errors import MediaError
from ..log import log
from ..media import probe
from .local import sha256_file
from .policy import check_url


class Downloader(Protocol):
    def download(self, video: VideoMeta, dest: Path) -> None:
        """Write the video to `dest` (an .mp4 path)."""


class YtDlpDownloader:
    """Downloads with yt-dlp. Needs a JavaScript runtime (deno or node) on PATH for YouTube."""

    def __init__(self, max_height: int = 1080):
        self.max_height = max_height

    def download(self, video: VideoMeta, dest: Path) -> None:
        import yt_dlp

        options = {
            "format": f"bv*[height<={self.max_height}]+ba/b[height<={self.max_height}]",
            "merge_output_format": "mp4",
            "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"deno": {}, "node": {}},
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([video.url])  # the canonical URL built from the API's video ID
        except yt_dlp.utils.DownloadError as exc:
            raise MediaError(f"download of {video.url} failed: {exc}") from exc
        if not dest.is_file():
            raise MediaError(f"download of {video.url} did not produce {dest.name}")


def ingest_url(
    db: DB,
    settings: Settings,
    url: str,
    lookup: Callable[[str], VideoMeta | None],
    downloader: Downloader,
) -> int:
    """Check policy, download, and record the source with the rule that allowed it."""
    decision, video = check_url(url, settings, lookup)
    decision.require()  # raises PolicyDenied before anything touches the disk

    existing = db.one("SELECT id, path FROM sources WHERE kind = 'url' AND url = ?", video.url)
    if existing and Path(existing["path"]).is_file():
        return existing["id"]

    dest = settings.library / f"{video.id}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    downloader.download(video, dest)
    info = probe(dest)
    if not info.has_video:
        dest.unlink()
        raise MediaError(f"{video.url} has no video stream")
    source_id = db.insert(
        "sources",
        kind="url",
        path=str(dest),
        url=video.url,
        channel=f"{video.channel_title} ({video.channel_id})",
        licence=video.licence,
        policy_rule=decision.rule,
        sha256=sha256_file(dest),
        duration_s=info.duration,
        ingested_at=time.time(),
    )
    log("source_ingested", url=video.url, rule=decision.rule, licence=video.licence, path=str(dest))
    return source_id
