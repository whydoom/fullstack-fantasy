"""Next week's pairings, for the site's Matchups to show who plays whom.

    python schedule_export.py                  # every league
    python schedule_export.py --league adn

The next week is the one after the latest final week the exports wrote
({platform}_luck.json). Yahoo's pairings come from the warehouse
(stg_yahoo__matchup_teams lands every scoreboard through the live week); ESPN's
from the league's schedule (the mMatchup view, one GET). Writes
data/processed/{league}/{platform}_schedule.json:

    {"league", "season", "week": 7, "games": [["11", "2"], ...]}

team ids as strings, in the platform's own game order; "games" is empty when the
week has none (the season is over). Also "last_regular_week" and "rest": every
regular-season week from the next one on, {week: games}, for the site's playoff
picture. Run after the other exports.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import duckdb

from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
PROCESSED = Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed")
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"


def yahoo_games(league: str, week: int) -> list[list[str]]:
    from src.ingest.discover import latest_league_key
    league_key = latest_league_key(LEAGUES[league]["yahoo_league_name"])
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        rows = con.execute("""
            select team_key, opponent_team_key from stg_yahoo__matchup_teams
            where league_key = ? and week = ? order by team_key""", [league_key, week]).fetchall()
    finally:
        con.close()
    tid = lambda key: key.split(".t.")[-1]   # noqa: E731
    games, seen = [], set()
    for a, b in rows:
        if a in seen or not b:
            continue
        seen |= {a, b}
        games.append([tid(a), tid(b)])
    return games


def yahoo_rest(league: str, week: int) -> tuple[int, dict]:
    """(last regular-season week, {week: games}) from `week` on, from Yahoo's API:
    the settings' playoff start, and each week's scoreboard (one GET a week)."""
    from src.ingest.discover import latest_league_key
    from src.ingest.yahoo_client import YahooFantasyClient
    from src.notify.live_final import entries, find, flat
    client = YahooFantasyClient()
    lk = latest_league_key(LEAGUES[league]["yahoo_league_name"])
    last = int(find(client.get(f"league/{lk}/settings"), "playoff_start_week")) - 1
    rest = {}
    for w in range(week, last + 1):
        games = []
        for m in entries(find(client.get(f"league/{lk}/scoreboard;week={w}"), "matchups") or {}, "matchup"):
            keys = [flat(t[0] if isinstance(t, list) else t).get("team_key")
                    for t in entries(find(m, "teams") or {}, "team")]
            keys = [k.split(".t.")[-1] for k in keys if k]
            if len(keys) == 2:
                games.append(keys)
        rest[str(w)] = games
    return last, rest


def espn_schedule(league: str) -> tuple[int, list]:
    """(last regular-season week, the season's schedule): one GET."""
    from src.ingest.espn import EspnClient, current_season
    client = EspnClient(league)
    data = client.get(f"seasons/{current_season()}/segments/0/leagues/{client.league_id}",
                      params=[("view", "mMatchup"), ("view", "mSettings")])
    data = data[0] if isinstance(data, list) and data else data
    last = int(((data.get("settings") or {}).get("scheduleSettings") or {}).get("matchupPeriodCount") or 0)
    return last, data.get("schedule", [])


def espn_games(schedule: list, week: int) -> list[list[str]]:
    return [[str(m["away"]["teamId"]), str(m["home"]["teamId"])]
            for m in schedule
            if m.get("matchupPeriodId") == week and m.get("away") and m.get("home")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES))
    for key in selected(ap.parse_args().league):
        platform = LEAGUES[key]["platform"]
        luck = json.loads((PROCESSED / key / f"{platform}_luck.json").read_text(encoding="utf-8"))
        week = max(int(w) for w in luck["weeks"]) + 1
        if platform == "yahoo":
            games = yahoo_games(key, week)
            last, rest = yahoo_rest(key, week)
        else:
            last, schedule = espn_schedule(key)
            games = espn_games(schedule, week)
            rest = {str(w): espn_games(schedule, w) for w in range(week, last + 1)}
        out = PROCESSED / key / f"{platform}_schedule.json"
        out.write_text(json.dumps({"league": key, "season": luck.get("season"), "week": week,
                                   "games": games, "last_regular_week": last, "rest": rest},
                                  indent=1) + "\n", encoding="utf-8")
        print(f"{key}: week {week}, {len(games)} games -> {out}")


if __name__ == "__main__":
    main()
