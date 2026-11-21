"""Publisher interface and the one function that publishes an approved render."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..db import DB
from ..errors import ClipforgeError
from ..log import log

PLATFORMS = ("youtube", "tiktok", "instagram")


@dataclass(frozen=True)
class Post:
    job_id: str
    video: Path
    title: str
    description: str  # already carries the source credit line, when there is one
    hashtags: list[str]

    @property
    def caption(self) -> str:
        """Description followed by hashtags, for platforms with a single caption field."""
        return f"{self.description}\n\n{' '.join(self.hashtags)}".strip()


@dataclass
class PublishResult:
    status: str  # "published" or "exported"
    post_id: str | None
    url: str | None
    detail: dict = field(default_factory=dict)


class Publisher(Protocol):
    platform: str

    def describe(self, post: Post) -> list[dict]:
        """The exact requests (or file writes) publish() would make, with credentials redacted."""

    def publish(self, post: Post) -> PublishResult: ...


def load_post(db: DB, job_id: str) -> Post:
    render = db.one("SELECT path, sidecar_path FROM renders WHERE job_id = ?", job_id)
    if render is None or not Path(render["path"]).is_file():
        raise ClipforgeError(f"job {job_id} has no render on disk")
    sidecar = json.loads(Path(render["sidecar_path"]).read_text())
    return Post(job_id, Path(render["path"]), sidecar["title"], sidecar["description"], sidecar["hashtags"])


def publish_job(db: DB, job_id: str, publisher: Publisher, dry_run: bool = False) -> PublishResult | list[dict]:
    """Publish an approved render to one platform.

    Only jobs a reviewer approved can be published; nothing here can approve one. With
    dry_run the requests are returned and nothing is sent or stored. Publishing the same
    job to the same platform twice is a no-op.
    """
    job = db.job(job_id)
    if job["state"] not in ("approved", "published"):
        raise ClipforgeError(f"job {job_id} is {job['state']}; only approved renders can be published")
    post = load_post(db, job_id)
    if dry_run:
        return publisher.describe(post)

    existing = db.one("SELECT * FROM posts WHERE job_id = ? AND platform = ?", job_id, publisher.platform)
    if existing:
        return PublishResult(existing["status"], existing["post_id"], existing["url"], {"already_done": True})

    result = publisher.publish(post)
    db.insert(
        "posts",
        job_id=job_id,
        platform=publisher.platform,
        status=result.status,
        post_id=result.post_id,
        url=result.url,
        detail=json.dumps(result.detail),
        created_at=time.time(),
    )
    if result.status == "published" and job["state"] == "approved":
        db.transition(job_id, "published")
    log("published", job_id, platform=publisher.platform, status=result.status, post_id=result.post_id, url=result.url)
    return result
