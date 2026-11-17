"""Every team's roster right now, with each player's injury status.

    python roster_status_export.py                  # every league
    python roster_status_export.py --league adn

For jimoody.com's bye grid (who is out in each of the next four weeks): the
current rosters (after the latest waivers, unlike the weekly grid) and each
player's status, in one shape for both platforms:

  "ir"   injured reserve, PUP, NFI or suspended: out for weeks
  "out"  out or doubtful: out this coming week
  "q"    questionable: expected to play
  ""     active

  Yahoo  league/{key}/teams/roster: player status (IR, O, D, Q, PUP-R, NFI-R, SUSP...)
  ESPN   the mRoster view: player.injuryStatus (INJURY_RESERVE, OUT, DOUBTFUL, ...)

Writes data/processed/{league}/{platform}_roster_status.json:

    {"league", "season", "teams": {"3": [{"id", "name", "pos", "status", "note"}, ...]}}

Player ids as the weekly grid has them (Yahoo player keys, ESPN ids). One or two
GETs a league; run with the other exports, Tuesday and Wednesday.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
PROCESSED = Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed")
IDP = {"DE", "DT", "LB", "CB", "S", "DB", "DL", "D"}
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
LONG = {"IR", "IR-R", "IR-NFI", "PUP", "PUP-R", "PUP-P", "NFI", "NFI-R", "NFI-A", "SUSP", "SUSPENSION",
        "INJURY_RESERVE", "COVID-19", "NA"}


def status_of(raw: str | None) -> str:
    s = (raw or "").upper()
    if s in LONG:
        return "ir"
    if s in ("O", "OUT", "D", "DOUBTFUL"):
        return "out"
    if s in ("Q", "QUESTIONABLE"):
        return "q"
    return ""


def yahoo_rosters(key: str) -> tuple[int, dict]:
    from src.ingest.discover import latest_league_key
    from src.ingest.espn import current_season
    from src.ingest.yahoo_client import YahooFantasyClient
    from src.notify.live_final import entries, find, flat
    client = YahooFantasyClient()
    lk = latest_league_key(LEAGUES[key]["yahoo_league_name"])
    d = client.get(f"league/{lk}/teams/roster")
    out = {}
    for t in entries(find(d, "teams"), "team"):
        tk = flat(t[0] if isinstance(t, list) else t).get("team_key")
        rows = []
        for p in entries(find(t, "players") or {}, "player"):
            f = flat(p[0] if isinstance(p, list) else p)
            pos = (f.get("display_position") or "").split(",")[0]
            pos = "DST" if pos == "DEF" else "IDP" if pos in IDP else pos
            rows.append({"id": f["player_key"], "name": (f.get("name") or {}).get("full"), "pos": pos,
                         "status": status_of(f.get("status")), "note": f.get("injury_note") or ""})
        out[tk.split(".t.")[-1]] = rows
    return current_season(), out


def espn_rosters(key: str) -> tuple[int, dict]:
    from src.ingest.espn import EspnClient, current_season
    client = EspnClient(key)
    season = current_season()
    d = client.get(f"seasons/{season}/segments/0/leagues/{client.league_id}", params=[("view", "mRoster")])
    d = d[0] if isinstance(d, list) and d else d
    out = {}
    for t in d.get("teams") or []:
        rows = []
        for e in (t.get("roster") or {}).get("entries") or []:
            p = (e.get("playerPoolEntry") or {}).get("player") or {}
            rows.append({"id": str(p.get("id")), "name": p.get("fullName"),
                         "pos": ESPN_POS.get(p.get("defaultPositionId"), "IDP"),
                         "status": status_of(p.get("injuryStatus")), "note": ""})
        out[str(t["id"])] = rows
    return season, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES))
    for key in selected(ap.parse_args().league):
        platform = LEAGUES[key]["platform"]
        season, teams = yahoo_rosters(key) if platform == "yahoo" else espn_rosters(key)
        out = PROCESSED / key / f"{platform}_roster_status.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"league": key, "season": season, "teams": teams}, indent=1) + "\n",
                       encoding="utf-8")
        n = sum(len(v) for v in teams.values())
        hurt = sum(1 for v in teams.values() for p in v if p["status"])
        print(f"{key}: {len(teams)} teams, {n} players, {hurt} with a status -> {out}")


if __name__ == "__main__":
    main()
