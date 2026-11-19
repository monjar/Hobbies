"""Source policy (PLAN.md constraint 1). Deny is the default; every allow names its rule."""

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import PolicyDenied

RULE_LOCAL_INBOX = "local_inbox"


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


def check_url(url: str, settings: Settings) -> Decision:
    """URL ingest (allowlisted channels, Creative Commons uploads) arrives in M4. Until then: deny."""
    return Decision(False, None, f"URL ingest is not implemented; {url} is discovery metadata only")
