"""Each league's draft: every pick, who took him, and (an auction) what he cost.

    python draft_export.py                  # every league
    python draft_export.py --league carrboro

jimoody.com's Draft Grades section ranks each pick against the player's value now.
The draft is fixed once it happens, but it is fetched each refresh (a few requests)
so a league added later, or a new season, needs nothing done by hand.

  ESPN   mDraftDetail: picks in order, with the bid in an auction; names and
         positions from the player info view.
  Yahoo  draftresults (pick, round, team, cost in an auction); names and positions
         from the league's players resource, 25 keys a request.

Writes data/processed/{league}/{platform}_draft.json:

    {"league", "season", "auction": bool,
     "picks": [{"pick", "round", "team", "player_id", "name", "pos", "bid", "keeper"}]}
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.ingest.espn import EspnClient, current_season
from src.ingest.espn_transactions import player_names
from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
PROCESSED = Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed")
IDP = {"DE", "DT", "LB", "CB", "S", "DB", "DL", "D"}


def espn_draft(key: str) -> dict:
    client = EspnClient(key)
    season = current_season()
    d = client.get(f"seasons/{season}/segments/0/leagues/{client.league_id}", params=[("view", "mDraftDetail")])
    d = d[0] if isinstance(d, list) and d else d
    picks = (d.get("draftDetail") or {}).get("picks") or []
    names = player_names(client, season, [p["playerId"] for p in picks])
    out = [{"pick": p["overallPickNumber"], "round": p["roundId"], "team": str(p["teamId"]),
            "player_id": str(p["playerId"]), "name": (names.get(p["playerId"]) or {}).get("name", "?"),
            "pos": (names.get(p["playerId"]) or {}).get("pos", "?"), "bid": p.get("bidAmount") or 0,
            "keeper": bool(p.get("keeper"))} for p in picks]
    return {"season": season, "auction": any(p["bid"] for p in out), "picks": out}


def yahoo_draft(key: str) -> dict:
    from src.ingest.discover import latest_league_key
    from src.ingest.yahoo_client import YahooFantasyClient
    from src.notify.live_final import entries, find, flat
    client = YahooFantasyClient()
    lk = latest_league_key(LEAGUES[key]["yahoo_league_name"])
    d = client.get(f"league/{lk}/draftresults")
    rows = [flat(r) for r in entries(find(d, "draft_results"), "draft_result")]
    rows = [r for r in rows if r.get("player_key")]
    info = {}
    keys = [r["player_key"] for r in rows]
    for i in range(0, len(keys), 25):
        got = client.get(f"league/{lk}/players;player_keys={','.join(keys[i:i + 25])}")
        for pl in entries(find(got, "players"), "player"):
            f = flat(pl)
            pos = (f.get("display_position") or "").split(",")[0]
            pos = "DST" if pos == "DEF" else "IDP" if pos in IDP else pos
            info[f["player_key"]] = {"name": (f.get("name") or {}).get("full", "?"), "pos": pos}
    out = [{"pick": int(r["pick"]), "round": int(r["round"]), "team": r["team_key"].split(".t.")[-1],
            "player_id": r["player_key"].split(".p.")[-1], "name": info.get(r["player_key"], {}).get("name", "?"),
            "pos": info.get(r["player_key"], {}).get("pos", "?"), "bid": int(r.get("cost") or 0), "keeper": False}
           for r in rows]
    return {"season": current_season(), "auction": any(p["bid"] for p in out), "picks": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES))
    for key in selected(ap.parse_args().league):
        platform = LEAGUES[key]["platform"]
        d = espn_draft(key) if platform == "espn" else yahoo_draft(key)
        out = PROCESSED / key / f"{platform}_draft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"league": key, **d}, indent=1) + "\n", encoding="utf-8")
        print(f"{key}: {len(d['picks'])} picks{' (auction)' if d['auction'] else ''} -> {out}")


if __name__ == "__main__":
    main()
