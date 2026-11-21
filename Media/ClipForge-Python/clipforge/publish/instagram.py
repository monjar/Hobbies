"""Instagram Reels through the content publishing API with a resumable upload. Per docs/integrations.md."""

import time

import httpx

from ..config import InstagramPublishCfg
from ..errors import ProviderError
from .base import Post, PublishResult

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
POLL_INTERVAL_S = 10.0
POLL_LIMIT_S = 300.0


class InstagramPublisher:
    platform = "instagram"

    def __init__(
        self,
        cfg: InstagramPublishCfg,
        access_token: str,
        user_id: str,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.cfg = cfg
        self._token = access_token
        self.user_id = user_id
        self._http = httpx.Client(timeout=httpx.Timeout(30.0, write=600.0, read=600.0), transport=transport)
        self._sleep = sleep

    def _container_body(self, post: Post) -> dict:
        body = {"media_type": "REELS", "upload_type": "resumable", "caption": post.caption}
        if self.cfg.ai_disclosure:
            body["is_ai_generated"] = True
        return body

    def describe(self, post: Post) -> list[dict]:
        version, size = self.cfg.api_version, post.video.stat().st_size
        return [
            {"method": "POST", "url": f"{GRAPH}/{version}/{self.user_id}/media",
             "headers": {"Authorization": "Bearer <access token>"}, "json": self._container_body(post)},
            {"method": "POST", "url": f"{RUPLOAD}/{version}/<container id>",
             "headers": {"Authorization": "OAuth <access token>", "offset": "0", "file_size": str(size)},
             "body": f"<bytes of {post.video}>"},
            {"method": "GET", "url": f"{GRAPH}/{version}/<container id>", "params": {"fields": "status_code"},
             "note": "repeated until status_code is FINISHED"},
            {"method": "POST", "url": f"{GRAPH}/{version}/{self.user_id}/media_publish",
             "headers": {"Authorization": "Bearer <access token>"}, "json": {"creation_id": "<container id>"}},
            {"method": "GET", "url": f"{GRAPH}/{version}/<media id>", "params": {"fields": "permalink"}},
        ]

    def _json(self, response: httpx.Response, step: str) -> dict:
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text[:200]
            raise ProviderError(f"instagram {step} failed ({response.status_code}): {message}")
        return response.json()

    def publish(self, post: Post) -> PublishResult:
        version = self.cfg.api_version
        bearer = {"Authorization": f"Bearer {self._token}"}
        try:
            container = self._json(
                self._http.post(f"{GRAPH}/{version}/{self.user_id}/media", headers=bearer, json=self._container_body(post)),
                "container creation",
            )
            container_id = container["id"]
            upload_url = container.get("uri") or f"{RUPLOAD}/{version}/{container_id}"
            self._json(
                self._http.post(
                    upload_url,
                    headers={"Authorization": f"OAuth {self._token}", "offset": "0",
                             "file_size": str(post.video.stat().st_size)},
                    content=post.video.read_bytes(),
                ),
                "upload",
            )
            waited = 0.0
            while True:
                status = self._json(
                    self._http.get(f"{GRAPH}/{version}/{container_id}", headers=bearer, params={"fields": "status_code"}),
                    "status check",
                ).get("status_code")
                if status == "FINISHED":
                    break
                if status in ("ERROR", "EXPIRED"):
                    raise ProviderError(f"instagram could not process the video (status {status})")
                if waited >= POLL_LIMIT_S:
                    raise ProviderError(f"instagram was still processing the video after {POLL_LIMIT_S:.0f}s")
                self._sleep(POLL_INTERVAL_S)
                waited += POLL_INTERVAL_S
            media_id = self._json(
                self._http.post(f"{GRAPH}/{version}/{self.user_id}/media_publish", headers=bearer,
                                json={"creation_id": container_id}),
                "publish",
            )["id"]
            # The reel is live at this point; a failed permalink lookup must not look like a failed publish.
            link = self._http.get(f"{GRAPH}/{version}/{media_id}", headers=bearer, params={"fields": "permalink"})
            url = link.json().get("permalink") if link.status_code == 200 else None
        except httpx.HTTPError as exc:
            raise ProviderError(f"instagram connection error: {type(exc).__name__}: {exc}") from exc
        return PublishResult("published", media_id, url, {"container_id": container_id})
