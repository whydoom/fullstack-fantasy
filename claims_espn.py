"""Every resolved waiver claim in each ESPN league, winners and losers.

    python claims_espn.py                        # every ESPN league
    python claims_espn.py --league college-forever

Writes data/processed/{league}/espn_claims.json:

    {"league", "season", "acquisition": {type, uses_budget, order_resets},
     "claims": [{"week", "player_id", "player", "pos", "team_id", "won",
                 "status", "bid", "when"}, ...]}

A claim is a WAIVER transaction that has been processed: EXECUTED (it won the
player) or FAILED_* (it lost -- FAILED_INVALIDPLAYERSOURCE means another team
got him first; FAILED_PLAYERALREADYDROPPED means the player the team meant to
drop was already gone). PENDING claims are other managers' live claims and are
never read; CANCELED ones were withdrawn and are left out too.

How many teams claimed the same player in the same scoring period measures how
wanted he was; in a league on traditional waivers (no budget) the winner is
decided by waiver priority alone, which the site plots against the standings.
GET requests only.
"""

from __future__ import annotations

import json

from espn_team_weeks import cli
from src.ingest import espn_transactions as tx
from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season


def claimed_id(t):
    """The player a claim was for: the ADD from the free-agent pool."""
    return next((i.get("playerId") for i in t.get("items") or []
                 if i.get("type") == "ADD" and (i.get("fromTeamId") or 0) == tx.POOL), None)


def acquisition(client, season):
    data = client.get(f"seasons/{season}/segments/0/leagues/{client.league_id}",
                      params=[("view", "mSettings")])
    obj = data[0] if isinstance(data, list) and data else data
    a = (obj.get("settings") or {}).get("acquisitionSettings") or {}
    return {"type": a.get("acquisitionType"), "uses_budget": bool(a.get("isUsingAcquisitionBudget")),
            "order_resets": bool(a.get("waiverOrderReset"))}


def main(league_key):
    season = current_season()
    client = EspnClient(league_key)
    raw = tx.collect(client, season)
    claims = [t for t in raw.values() if t.get("type") == "WAIVER"
              and (t.get("status") == "EXECUTED" or str(t.get("status") or "").startswith("FAILED"))]
    names = tx.player_names(client, season, {claimed_id(t) for t in claims})
    out_claims = []
    for t in sorted(claims, key=lambda t: (t.get("scoringPeriodId") or 0, t.get("processDate") or 0)):
        pid = claimed_id(t)
        meta = names.get(pid, {"name": f"#{pid}", "pos": "?"})
        out_claims.append({
            "week": t.get("scoringPeriodId"),
            "player_id": pid, "player": meta["name"], "pos": meta["pos"],
            "team_id": t.get("teamId"),
            "won": t.get("status") == "EXECUTED",
            "status": t.get("status"),
            "bid": t.get("bidAmount") or 0,
            "when": tx.when(t.get("processDate")),
        })
    out = PROJECT_ROOT / "data" / "processed" / league_key / "espn_claims.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"league": league_key, "season": season,
                               "acquisition": acquisition(client, season),
                               "claims": out_claims}, indent=2), encoding="utf-8")
    won = sum(c["won"] for c in out_claims)
    print(f"{league_key}: {len(out_claims)} resolved claims ({won} won, {len(out_claims) - won} lost) "
          f"-> {out.name}")


if __name__ == "__main__":
    cli(main)
