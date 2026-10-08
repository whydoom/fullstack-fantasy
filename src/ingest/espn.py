"""ESPN Fantasy Football ingestion.

ESPN has no developer program: reads go to lm-api-reads.fantasy.espn.com and are
authorized with the espn_s2 and SWID cookies from a live browser session. There
is no refresh flow -- when the cookies die they must be re-harvested by hand, so
this module fails loudly on an auth error rather than returning stale data.

Design matches src/ingest/nflverse.py and yahoo_client.py:
  * raw JSON is landed untouched; dbt does the shaping
  * completed seasons are write-once, the IN-PROGRESS season always refetches
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.ingest.leagues import DEFAULT_LEAGUE, league

load_dotenv()

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "espn"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def raw_dir(league_key: str) -> Path:
    """data/raw/espn/{league_key} -- seasons and player pools nest under it."""
    return RAW_DIR / league_key


def processed_dir(league_key: str) -> Path:
    return PROCESSED_DIR / league_key


MIN_SECONDS_BETWEEN_CALLS = 1.0

# The NFL season rolls over in the new year, so Jan-Jun still belongs to the
# prior season's league year.
def current_season() -> int:
    now = datetime.now()
    return now.year if now.month >= 7 else now.year - 1


DEFAULT_VIEWS = [
    "mSettings",      # scoring rules, roster slots, playoff structure
    "mTeam",          # teams, managers, records
    "mRoster",        # rosters -- started vs benched
    "mMatchup",       # head-to-head results
    "mDraftDetail",   # draft picks
    "mTransactions2", # adds, drops, trades
]


class EspnAuthError(RuntimeError):
    pass


class EspnClient:
    def __init__(self, league_key: str = DEFAULT_LEAGUE) -> None:
        cfg = league(league_key)
        self.league_key = league_key
        self.league_id = str(cfg["league_id"])
        self.raw_dir = raw_dir(league_key)

        # Cookies are per league: resolved from the creds prefix, never in code.
        prefix = cfg["creds"]
        names = (f"ESPN_S2_{prefix}", f"ESPN_SWID_{prefix}")
        missing = [n for n in names if not os.environ.get(n, "").strip()]
        if missing:
            raise EspnAuthError(
                f"League {league_key!r} needs {' and '.join(missing)} in .env "
                f"(see .env.example)."
            )
        s2, swid = (os.environ[n].strip() for n in names)
        self.cookies = {"espn_s2": s2, "SWID": swid}
        self.session = requests.Session()
        # ESPN rejects the default python-requests user agent on some paths.
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        })
        self._last_call_at = 0.0

    def get(self, path: str, params: dict | None = None) -> dict | list:
        elapsed = time.time() - self._last_call_at
        if elapsed < MIN_SECONDS_BETWEEN_CALLS:
            time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

        url = f"{BASE}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, cookies=self.cookies, timeout=30)
        self._last_call_at = time.time()

        if resp.status_code in (401, 403):
            raise EspnAuthError(
                f"ESPN returned {resp.status_code} for {resp.url}\n"
                f"{resp.text[:400]}\n\n"
                "Cookies are most likely expired. Re-harvest espn_s2 and SWID from "
                "DevTools (Application > Cookies > espn.com) and update .env."
            )
        if not resp.ok:
            # Keep the body: a status code alone is not a diagnosis.
            raise requests.HTTPError(
                f"{resp.status_code} for {resp.url}\n{resp.text[:600]}", response=resp
            )
        return resp.json()

    # ---------- season fetches ----------

    def season(self, year: int, views: list[str] | None = None) -> dict | list:
        """Current season uses the live league path; prior seasons use leagueHistory.

        Note the shape differs: leagueHistory returns a LIST of season objects,
        the live path returns a single object. Landed raw either way.
        """
        views = views or DEFAULT_VIEWS
        if year == current_season():
            return self.get(
                f"seasons/{year}/segments/0/leagues/{self.league_id}",
                params=[("view", v) for v in views],
            )
        return self.get(
            f"leagueHistory/{self.league_id}",
            params=[("seasonId", year)] + [("view", v) for v in views],
        )

    def fetch_and_land(self, year: int, views: list[str] | None = None) -> dict | list:
        """Land a season's payload. Completed seasons are never refetched; the
        in-progress season always is, because it changes every week."""
        target = self.raw_dir / str(year) / "league.json"
        is_current = year == current_season()

        if target.exists() and not is_current:
            return json.loads(target.read_text())["data"]

        payload = self.season(year, views)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "_meta": {
                "league_key": self.league_key,
                "league_id": self.league_id,
                "season": year,
                "views": views or DEFAULT_VIEWS,
                "is_current_season": is_current,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            "data": payload,
        }, indent=2))
        print(f"  landed {target.relative_to(PROJECT_ROOT)}"
              f"{' (current season, refetched)' if is_current else ''}")
        return payload
