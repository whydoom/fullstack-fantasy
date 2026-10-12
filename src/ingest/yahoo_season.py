"""Land one Yahoo league-season's raw payloads: the repeatable form of what feeds dbt.

    python -m src.ingest.yahoo_season 470.l.251277

Lands, under data/raw/{season}/{league_key}/:

    settings.json                         league settings (weeks, roster slots, FAAB)
    teams.json                            teams and managers
    transactions.json                     the full add/drop/trade log
    scoreboard_week_{NN}.json             each week's matchups and official scores
    rosters/week_{NN}/{team_key}.json     each team's lineup and player points, weekly
    players/week_{NN}/{POS}_{start}.json  top players by points, per position, weekly
    players/season/{POS}_{start}.json     top players by season-to-date points

What gets re-fetched (refresh=True) and what lands once
-------------------------------------------------------
fetch_and_land skips a file that already exists, which is right for anything that
can no longer change and wrong for anything that can. The rule here:

  * A finished season (settings.is_finished) is immutable: everything lands once.
  * In an unfinished season, settings, teams, transactions and season-to-date
    players always re-fetch: they change all season.
  * Weekly resources re-fetch for the current week AND the week before it. The
    current week is still being played. The previous one is over, but Yahoo applies
    NFL stat corrections for several days after a week ends, so a week fetched on
    Monday night can still change. Two weeks back, it is treated as settled.

The season is looked up in the discovery manifest rather than derived: Yahoo's game
key (the "470" in 470.l.251277) changes every season and maps to a year only through
the API, which src/ingest/discover.py already records. Run discovery first.

Read-only, like everything under src/ingest: GET requests only, via
YahooFantasyClient.get.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

from src.ingest.discover import MANIFEST_PATH
from src.ingest.yahoo_client import YahooFantasyClient

# Leaders depth per position. 50 comfortably covers a 12-team league's starters and
# the waiver-relevant tier behind them; Yahoo pages players 25 at a time.
PLAYER_POOL_DEPTH = 50
PAGE_SIZE = 25

# Roster slots that are not real positions and cannot filter the player pool:
# bench, injured reserve, and multi-position flex slots like W/R/T.
NON_POSITION_SLOTS = {"BN", "IR"}

# Weeks this far behind the current one still re-fetch (stat corrections).
WEEKS_STILL_CHANGING = 1


def _walk(node: Any, key: str) -> Iterator[dict[str, Any]]:
    """Yield every dict containing `key`, wherever it sits (see discover.py)."""
    if isinstance(node, dict):
        if key in node:
            yield node
        for value in node.values():
            yield from _walk(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def _one(payload: Any, key: str) -> Any:
    """The single value of `key` in a payload. Fails if it is missing or repeated."""
    values = {json.dumps(d[key], sort_keys=True) for d in _walk(payload, key)}
    if len(values) != 1:
        raise ValueError(f"Expected one {key!r} in payload, found {len(values)}.")
    return json.loads(values.pop())


def _is_finished(settings: Any) -> bool:
    """Yahoo only includes is_finished once a season ends; absent means in progress."""
    return any(str(d["is_finished"]) == "1" for d in _walk(settings, "is_finished"))


def season_for(league_key: str) -> str:
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"No discovery manifest at {MANIFEST_PATH}. "
            "Run `python -m src.ingest.discover` first."
        )
    for league in json.loads(MANIFEST_PATH.read_text()):
        if league.get("league_key") == league_key:
            return str(league["season"])
    raise SystemExit(
        f"{league_key} is not in the discovery manifest. Re-run discovery if the "
        "league is new this season."
    )


def land_season(league_key: str, client: YahooFantasyClient | None = None) -> None:
    client = client or YahooFantasyClient()
    season = season_for(league_key)
    root = f"{league_key}"

    def land(path: str, name: str, refresh: bool) -> Any:
        print(f"  {'fetch ' if refresh else 'cached'}  {name}")
        return client.fetch_and_land(path, season, f"{root}/{name}", refresh=refresh)

    # Settings first: they decide what is still changing. A landed settings file
    # that already says the season is finished is final and is not re-fetched.
    settings_file = Path("data/raw") / season / root / "settings.json"
    finished_on_disk = settings_file.exists() and _is_finished(
        json.loads(settings_file.read_text())["data"])
    settings = land(f"league/{league_key}/settings", "settings", not finished_on_disk)

    is_finished = _is_finished(settings)
    live = not is_finished
    last_week = int(_one(settings, "end_week") if is_finished else _one(settings, "current_week"))
    positions = [
        rp["roster_position"]["position"]
        for rp in _walk(settings, "roster_position")
        if isinstance(rp["roster_position"], dict)
        and rp["roster_position"]["position"] not in NON_POSITION_SLOTS
        and "/" not in rp["roster_position"]["position"]
    ]
    print(f"{league_key}  season {season}  through week {last_week}  "
          f"{'in progress' if live else 'finished'}  positions {positions}")

    teams = land(f"league/{league_key}/teams", "teams", live)
    team_keys = sorted(
        {d["team_key"] for d in _walk(teams, "team_key")},
        key=lambda k: int(k.rsplit(".", 1)[1]),
    )
    land(f"league/{league_key}/transactions", "transactions", live)

    for week in range(1, last_week + 1):
        changing = live and week >= last_week - WEEKS_STILL_CHANGING
        ww = f"week_{week:02d}"
        land(f"league/{league_key}/scoreboard;week={week}", f"scoreboard_{ww}", changing)
        for team_key in team_keys:
            land(f"team/{team_key}/roster;week={week}/players/stats;type=week;week={week}",
                 f"rosters/{ww}/{team_key}", changing)
        for pos in positions:
            for start in range(0, PLAYER_POOL_DEPTH, PAGE_SIZE):
                land(f"league/{league_key}/players;position={pos};sort=PTS;sort_type=week;"
                     f"sort_week={week};start={start};count={PAGE_SIZE}/stats;type=week;week={week}",
                     f"players/{ww}/{pos}_{start:02d}", changing)

    for pos in positions:
        for start in range(0, PLAYER_POOL_DEPTH, PAGE_SIZE):
            land(f"league/{league_key}/players;position={pos};sort=PTS;sort_type=season;"
                 f"start={start};count={PAGE_SIZE}/stats;type=season",
                 f"players/season/{pos}_{start:02d}", live)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m src.ingest.yahoo_season <league_key>\n"
              "  e.g. python -m src.ingest.yahoo_season 470.l.251277\n"
              "  League keys come from `python -m src.ingest.discover`.")
        raise SystemExit(2)
    land_season(sys.argv[1])


if __name__ == "__main__":
    main()
