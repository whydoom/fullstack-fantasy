"""ESPN's own standings order, for leagues whose site standings mirror the platform.

    python standings_espn.py                         # every ESPN league
    python standings_espn.py --league college-forever

Writes data/processed/{league}/espn_standings.json:

    {"league", "season", "through_week", "seeds": {team_id: seed},
     "records": {team_id: "W-L[-T]"}}

`seeds` is ESPN's playoffSeed -- the order the league's standings page shows,
which applies the league's own rules (divisions, tiebreakers) that cannot be
rebuilt from scores alone. `through_week` is how many games the teams have
played, so the site uses the order only for the week it describes. A league
uses it when its league-list entry says `"standings": "platform"`. GET only.
"""

from __future__ import annotations

import json

from espn_team_weeks import cli
from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season


def main(league_key):
    season = current_season()
    client = EspnClient(league_key)
    data = client.get(f"seasons/{season}/segments/0/leagues/{client.league_id}",
                      params=[("view", "mTeam")])
    obj = data[0] if isinstance(data, list) and data else data
    seeds, records, played = {}, {}, set()
    for t in obj.get("teams") or []:
        rec = (t.get("record") or {}).get("overall") or {}
        w, l, ties = rec.get("wins", 0), rec.get("losses", 0), rec.get("ties", 0)
        seeds[str(t["id"])] = t.get("playoffSeed")
        records[str(t["id"])] = f"{w}-{l}" + (f"-{ties}" if ties else "")
        played.add(w + l + ties)
    if len(played) != 1:
        raise SystemExit(f"{league_key}: teams have played different numbers of games {sorted(played)}; "
                         "run after the week is final.")
    out = PROJECT_ROOT / "data" / "processed" / league_key / "espn_standings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"league": league_key, "season": season, "through_week": played.pop(),
                               "seeds": seeds, "records": records}, indent=2), encoding="utf-8")
    order = sorted(seeds, key=lambda t: seeds[t])
    print(f"{league_key}: ESPN standings through week {json.loads(out.read_text())['through_week']}: "
          f"{', '.join(order)} -> {out.name}")


if __name__ == "__main__":
    cli(main)
