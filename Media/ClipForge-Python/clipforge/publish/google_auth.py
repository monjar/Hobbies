"""Google OAuth for an installed app: loopback redirect with PKCE, and refresh-token storage.

Flow and endpoints per docs/integrations.md.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

from ..errors import ClipforgeError, ConfigError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",  # ask for a refresh token
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class GoogleAuth:
    def __init__(self, client_id: str, client_secret: str, token_path: Path, transport: httpx.BaseTransport | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_path = token_path
        self._http = httpx.Client(timeout=30.0, transport=transport)
        self._access: tuple[str, float] | None = None

    def _token_request(self, fields: dict) -> dict:
        try:
            response = self._http.post(
                TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret, **fields}
            )
        except httpx.HTTPError as exc:
            raise ClipforgeError(f"google token endpoint unreachable: {type(exc).__name__}") from None
        if response.status_code != 200:
            try:
                reason = response.json().get("error_description") or response.json().get("error")
            except Exception:
                reason = response.text[:200]
            raise ClipforgeError(f"google token request failed ({response.status_code}): {reason}")
        return response.json()

    def _save(self, refresh_token: str) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps({"refresh_token": refresh_token}))
        os.chmod(self.token_path, 0o600)

    def login(self, open_url, timeout_s: float = 300.0) -> None:
        """Run the browser consent flow. `open_url(url)` shows the consent page to the user."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        received: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
                received.update(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<p>clipforge is connected. You can close this tab.</p>")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1.0
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
        try:
            open_url(authorization_url(self.client_id, redirect_uri, challenge, state))
            deadline = time.monotonic() + timeout_s
            while not received and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not received:
            raise ClipforgeError("no response from the Google consent page before the timeout")
        if received.get("state") != state:
            raise ClipforgeError("the consent response did not match this login attempt")
        if "code" not in received:
            raise ClipforgeError(f"Google consent was not granted: {received.get('error', 'no code returned')}")
        tokens = self._token_request(
            {"code": received["code"], "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        )
        if not tokens.get("refresh_token"):
            raise ClipforgeError("Google returned no refresh token; remove the app's access in your Google account and retry")
        self._save(tokens["refresh_token"])
        self._access = (tokens["access_token"], time.monotonic() + tokens.get("expires_in", 3600) - 60)

    def access_token(self) -> str:
        if self._access and time.monotonic() < self._access[1]:
            return self._access[0]
        if not self.token_path.is_file():
            raise ConfigError("not connected to YouTube; run `clipforge auth youtube` first")
        refresh_token = json.loads(self.token_path.read_text())["refresh_token"]
        tokens = self._token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})
        self._access = (tokens["access_token"], time.monotonic() + tokens.get("expires_in", 3600) - 60)
        return self._access[0]
