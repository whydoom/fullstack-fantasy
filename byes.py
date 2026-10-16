"""Each rostered player's NFL bye week(s), per league, from data already landed.

    python byes.py                    # every league
    python byes.py --league adn

Writes data/processed/{league}/{platform}_byes.json:

    {"league", "season", "byes": {player_id: [week, ...]}}

for every player on a fantasy roster this season (the ids in that league's
team_weeks grid). The site's Roster by week tags a player who did not play in
one of his bye weeks BYE instead of DNP.

  yahoo  each roster download (data/raw/{season}/{league_key}/rosters/week_NN/)
         carries the player's `bye_weeks` -- no API call.
  espn   the landed player pools (data/raw/espn/{league}/{season}/player_pool/)
         carry each player's NFL team (proTeamId) week by week; ESPN's pro-team
         schedule maps a team to its bye -- one GET (proTeamSchedules_wl).

A player who changed NFL teams between two with different byes gets both weeks.
Reads only what is already landed (plus that one read-only request), so it can
run any day of the week without pulling a partial week.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ingest.discover import latest_league_key
from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season, processed_dir
from src.ingest.leagues import LEAGUES, selected

RAW = PROJECT_ROOT / "data" / "raw"


def grid_ids(league_key):
    """Player ids on any roster this season, from the league's team_weeks export."""
    platform = LEAGUES[league_key]["platform"]
    tw = json.loads((processed_dir(league_key) / f"{platform}_team_weeks.json").read_text(encoding="utf-8"))
    return tw["season"], {str(p["id"]) for ps in tw["grid"].values() for p in ps}


def yahoo_players(node):
    """Every player record in a Yahoo payload, its list-of-fragments merged into
    one dict ([{"player_key": ...}, {"name": ...}, {"bye_weeks": ...}, ...])."""
    if isinstance(node, list):
        if any(isinstance(x, dict) and "player_key" in x for x in node):
            merged = {}
            for x in node:
                if isinstance(x, dict):
                    merged.update(x)
            yield merged
        for x in node:
            yield from yahoo_players(x)
    elif isinstance(node, dict):
        for v in node.values():
            yield from yahoo_players(v)


def yahoo_byes(league_key, season):
    lk = latest_league_key(LEAGUES[league_key]["yahoo_league_name"])
    out = {}
    for f in sorted((RAW / str(season) / lk / "rosters").glob("week_*/*.json")):
        for p in yahoo_players(json.loads(f.read_text(encoding="utf-8"))):
            wk = (p.get("bye_weeks") or {}).get("week")
            if wk and str(wk).isdigit():
                out.setdefault(p["player_key"], set()).add(int(wk))
    return out


def espn_byes(league_key, season):
    data = EspnClient(league_key).get(f"seasons/{season}", params=[("view", "proTeamSchedules_wl")])
    obj = data[0] if isinstance(data, list) and data else data
    team_bye = {t["id"]: t.get("byeWeek") for t in (obj.get("settings") or {}).get("proTeams") or []
                if t.get("id")}
    out = {}
    for f in sorted((RAW / "espn" / league_key / str(season) / "player_pool").glob("week*.json")):
        pool = json.loads(f.read_text(encoding="utf-8"))
        pool = pool[0] if isinstance(pool, list) and pool else pool
        for e in pool.get("players") or []:
            p = e.get("player") or {}
            bye = team_bye.get(p.get("proTeamId"))
            if p.get("id") and bye:
                out.setdefault(str(p["id"]), set()).add(int(bye))
    return out


def main(league_key):
    season, ids = grid_ids(league_key)
    platform = LEAGUES[league_key]["platform"]
    found = (yahoo_byes if platform == "yahoo" else espn_byes)(league_key, season)
    byes = {pid: sorted(found[pid]) for pid in sorted(ids) if pid in found}
    out = processed_dir(league_key) / f"{platform}_byes.json"
    out.write_text(json.dumps({"league": league_key, "season": season, "byes": byes}, indent=2),
                   encoding="utf-8")
    two = sum(1 for v in byes.values() if len(v) > 1)
    print(f"{league_key}: bye weeks for {len(byes)} of {len(ids)} rostered players"
          f"{f' ({two} with two)' if two else ''} -> {out.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES), help="league key (default: every league)")
    for key in selected(ap.parse_args().league):
        main(key)
