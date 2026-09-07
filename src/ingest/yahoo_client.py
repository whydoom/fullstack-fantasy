"""OAuth2 client for the Yahoo Fantasy Sports API.

Read-only. This module never calls a write endpoint (no roster moves, no trades,
no league-setting changes) — only GET requests against fantasysports.yahooapis.com.

Deliberately raw: every method here returns Yahoo's JSON untouched. Parsing belongs
in dbt (staging models), not here — see CLAUDE.md's ELT rationale. Do not reach for
yfpy/yahoofantasy; they parse the payload into objects before you ever see the raw
shape, which is exactly what this project needs to avoid.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

TOKEN_PATH = Path(".secrets/token.json")
RAW_DATA_DIR = Path("data/raw")

# Yahoo has no documented rate limit for this API, but there is no reason to hammer
# a personal league's data with a personal script.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

# Refresh proactively once the access token is within this margin of expiring, so a
# long-running backfill doesn't get killed by expiry mid-run.
TOKEN_REFRESH_MARGIN_SECONDS = 60


@dataclass
class YahooCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "YahooCredentials":
        load_dotenv()
        client_id = os.environ.get("YAHOO_CLIENT_ID")
        client_secret = os.environ.get("YAHOO_CLIENT_SECRET")
        redirect_uri = os.environ.get("YAHOO_REDIRECT_URI", "https://localhost:8080")
        if not client_id or not client_secret:
            raise RuntimeError(
                "YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET must be set (see .env.example)."
            )
        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


class YahooFantasyClient:
    """Thin, raw-payload-preserving client for the Yahoo Fantasy Sports API."""

    def __init__(self, credentials: YahooCredentials | None = None):
        self.credentials = credentials or YahooCredentials.from_env()
        self._session = requests.Session()
        self._last_request_time: float = 0.0
        self._token: dict[str, Any] = self._load_token()

    # ---------------------------------------------------------------- OAuth

    def _load_token(self) -> dict[str, Any]:
        if TOKEN_PATH.exists():
            return json.loads(TOKEN_PATH.read_text())
        return {}

    def _save_token(self, token: dict[str, Any]) -> None:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(json.dumps(token, indent=2))
        # chmod 600: the token grants API access to a personal league; this repo is
        # public, so the file must never be readable by other local accounts either.
        os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)

    def authorize_interactive(self) -> None:
        """Run the three-legged OAuth2 handshake, prompting the user to paste the code.

        redirect_uri (https://localhost:8080) has nothing listening on it by design —
        this is a personal script, not a registered web app with a live callback.
        Yahoo redirects the browser there anyway and the `code` query param is visible
        in the address bar even though the page fails to load; the user copies it out.
        """
        params = {
            "client_id": self.credentials.client_id,
            "redirect_uri": self.credentials.redirect_uri,
            "response_type": "code",
            "language": "en-us",
        }
        request = requests.Request("GET", AUTH_URL, params=params).prepare()
        print(f"Opening browser for Yahoo authorization:\n{request.url}\n")
        webbrowser.open(request.url)
        code = input("Paste the `code` value from the redirected URL: ").strip()

        response = self._session.post(
            TOKEN_URL,
            data={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "redirect_uri": self.credentials.redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        token = response.json()
        token["obtained_at"] = time.time()
        self._token = token
        self._save_token(token)

    def _refresh_token(self) -> None:
        if not self._token.get("refresh_token"):
            raise RuntimeError(
                "No refresh_token on file. Run authorize_interactive() once first."
            )
        response = self._session.post(
            TOKEN_URL,
            data={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "redirect_uri": self.credentials.redirect_uri,
                "refresh_token": self._token["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        new_token = response.json()
        new_token["obtained_at"] = time.time()
        # Yahoo may omit refresh_token in a refresh response, meaning "unchanged" —
        # not "revoked". Losing it here would force a full re-auth for no reason.
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = self._token["refresh_token"]
        self._token = new_token
        self._save_token(new_token)

    def _ensure_fresh_token(self) -> None:
        if not self._token:
            raise RuntimeError(
                "No token on file. Run authorize_interactive() once first."
            )
        obtained_at = self._token.get("obtained_at", 0)
        expires_in = self._token.get("expires_in", 0)
        expires_at = obtained_at + expires_in
        if time.time() >= expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            self._refresh_token()

    # ------------------------------------------------------------- Requests

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)

    def get(self, resource_path: str) -> dict[str, Any]:
        """GET a resource path relative to API_BASE, appending ?format=json.

        Read-only by construction: this method issues GET requests only. Do not add
        a post()/put()/delete() to this client — this project must never write to a
        Yahoo league.
        """
        self._ensure_fresh_token()
        self._throttle()

        separator = "&" if "?" in resource_path else "?"
        url = f"{API_BASE}/{resource_path.lstrip('/')}{separator}format=json"
        headers = {"Authorization": f"Bearer {self._token['access_token']}"}
        response = self._session.get(url, headers=headers)
        self._last_request_time = time.time()

        if response.status_code == 401:
            # Access token may have been invalidated out-of-band; refresh once and
            # retry a single time before giving up.
            self._refresh_token()
            headers = {"Authorization": f"Bearer {self._token['access_token']}"}
            response = self._session.get(url, headers=headers)
            self._last_request_time = time.time()

        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------- Landing

    def fetch_and_land(self, resource_path: str, season: str, name: str) -> dict[str, Any]:
        """Fetch a resource, persist it (wrapped with _meta) to data/raw/{season}/{name}.json,
        and return the untouched Yahoo payload — not the on-disk _meta wrapper.

        Idempotent: if the file already exists, it is read back and unwrapped instead
        of re-fetching. Completed seasons are immutable in Yahoo's API, so this makes
        a historical backfill safe to re-run after a crash — it costs exactly one pass
        over the API.
        """
        dest = RAW_DATA_DIR / season / f"{name}.json"
        if dest.exists():
            return json.loads(dest.read_text())["data"]

        payload = self.get(resource_path)
        wrapped = {
            "_meta": {
                "resource_path": resource_path,
                "season": season,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            "data": payload,
        }

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(wrapped, indent=2))
        return payload


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "auth":
        YahooFantasyClient().authorize_interactive()
        return

    print(
        "Usage: python -m src.ingest.yahoo_client auth\n"
        "  auth  Run the interactive OAuth2 handshake and cache the token to "
        f"{TOKEN_PATH}"
    )


if __name__ == "__main__":
    main()
