"""Source policy (PLAN.md constraint 1). Deny is the default; every allow names its rule."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..db import DB
from ..discover.youtube import LICENCE_CC, VideoMeta, video_id_from_url
from ..errors import PolicyDenied

RULE_LOCAL_INBOX = "local_inbox"
RULE_ALLOWLISTED_CHANNEL = "allowlisted_channel"
RULE_CREATIVE_COMMONS = "creative_commons"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str | None
    reason: str

    def require(self) -> "Decision":
        if not self.allowed:
            raise PolicyDenied(self.reason)
        return self


def check_local(path: Path | str, settings: Settings) -> Decision:
    """Local footage is allowed only if the real file sits inside the configured inbox."""
    resolved = Path(path).expanduser().resolve()  # follows symlinks, so a link out of the inbox is denied
    if not resolved.is_file():
        return Decision(False, None, f"{path} is not a file")
    if not resolved.is_relative_to(settings.inbox):
        return Decision(False, None, f"{resolved} is outside the inbox folder {settings.inbox}")
    return Decision(True, RULE_LOCAL_INBOX, f"file is inside {settings.inbox}")


def check_library(path: Path | str, settings: Settings, db: DB, sha256: str) -> Decision:
    """A file in the library is allowed only if it is a recorded URL ingest and is unchanged."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or not resolved.is_relative_to(settings.library.resolve()):
        return Decision(False, None, f"{resolved} is not inside the inbox or the library")
    row = db.one("SELECT policy_rule, sha256 FROM sources WHERE kind = 'url' AND path = ?", str(resolved))
    if row is None:
        return Decision(False, None, f"{resolved} was not ingested through the source policy")
    if row["sha256"] != sha256:
        return Decision(False, None, f"{resolved} has changed since it was ingested")
    return Decision(True, row["policy_rule"], "recorded URL ingest")


def check_url(
    url: str, settings: Settings, lookup: Callable[[str], VideoMeta | None]
) -> tuple[Decision, VideoMeta | None]:
    """Decide whether a URL may be downloaded.

    `lookup` asks the YouTube Data API about a video ID. Allowed only when the API says the
    video is from an allowlisted channel or is Creative Commons. Everything else is denied,
    including any URL that is not a single YouTube video.
    """
    video_id = video_id_from_url(url)
    if video_id is None:
        return Decision(False, None, f"{url} is not a YouTube video URL; it is discovery metadata only"), None
    video = lookup(video_id)
    if video is None:
        return Decision(False, None, f"the YouTube API returned no video for {video_id}"), None
    if video.channel_id in settings.sources.allowlist_channels:
        return Decision(True, RULE_ALLOWLISTED_CHANNEL, f"channel {video.channel_id} is allowlisted"), video
    if settings.sources.allow_creative_commons and video.licence == LICENCE_CC:
        return Decision(True, RULE_CREATIVE_COMMONS, "the YouTube API reports a Creative Commons licence"), video
    return (
        Decision(
            False, None,
            f"{video.url} is not allowed: channel {video.channel_id} is not allowlisted and the licence is "
            f"{video.licence!r}",
        ),
        video,
    )
