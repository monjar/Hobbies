"""Optional notification: POST a short JSON message to a webhook (Slack-compatible `text` field)."""

import httpx

from .errors import ClipforgeError


def send_webhook(url: str, text: str, transport: httpx.BaseTransport | None = None) -> None:
    try:
        response = httpx.Client(timeout=15.0, transport=transport).post(url, json={"text": text})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ClipforgeError(f"notification failed: {type(exc).__name__}") from exc
