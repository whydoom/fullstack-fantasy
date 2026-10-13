"""Every player each team added this season, for every ESPN league: waiver claims
(with their winning bid, if the league uses FAAB) and free-agent adds ($0).

    python pickups_espn.py                       # every ESPN league
    python pickups_espn.py --league college-forever

Writes data/processed/{league}/espn_pickups.json:

    {"league", "season", "teams": {team_id: [
        {"week", "player", "pos", "bid", "source", "dropped", "when"}, ...]}}

`source` is "waivers" for a claim that went through the waiver process and
"free agent" for a straight add from the pool; `bid` is the amount paid, 0 for
a free add or in a league without FAAB. Only EXECUTED acquisitions count -- a
failed or pending claim added nobody (and pending ones are other managers' live
bids). The site's Tuesday recap reads this file. GET requests only.
"""

from __future__ import annotations

import json

from espn_team_weeks import cli
from src.ingest import espn_transactions as tx
from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season


def main(league_key):
    season = current_season()
    client = EspnClient(league_key)
    raw = tx.collect(client, season)
    acqs = [t for t in raw.values()
            if tx.classify(t) == "ACQUISITION" and t.get("status") == "EXECUTED"]
    names = tx.player_names(client, season,
                            {pid for t in acqs for pid in (tx.added_id(t), tx.dropped_id(t))})
    teams = {}
    for t in sorted(acqs, key=lambda t: t.get("processDate") or 0):
        add = names.get(tx.added_id(t), {"name": f"#{tx.added_id(t)}", "pos": "?"})
        drop = names.get(tx.dropped_id(t)) if tx.dropped_id(t) else None
        teams.setdefault(str(t.get("teamId")), []).append({
            "week": t.get("scoringPeriodId"),
            "player": add["name"], "pos": add["pos"],
            "bid": t.get("bidAmount") or 0,
            "source": "waivers" if t.get("type") == "WAIVER" else "free agent",
            "dropped": (drop or {}).get("name"),
            "when": tx.when(t.get("processDate")),
        })
    out = PROJECT_ROOT / "data" / "processed" / league_key / "espn_pickups.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"league": league_key, "season": season, "teams": teams},
                              indent=2), encoding="utf-8")
    n = sum(len(v) for v in teams.values())
    paid = sum(1 for v in teams.values() for p in v if p["bid"])
    print(f"{league_key}: {n} pickups ({paid} with a bid, {n - paid} at $0) -> {out.name}")


if __name__ == "__main__":
    cli(main)
