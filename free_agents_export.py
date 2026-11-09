"""The best free agents each week: who a team could have claimed off waivers.

    python free_agents_export.py                  # every league
    python free_agents_export.py --league adn

For each final week and each position, the players on no roster in the league that
week (rosters from {platform}_team_weeks.json), the TOP best by projection, each
with his projection and the points he actually scored -- in the league's scoring.
jimoody.com's Trades section claims from it when a team, without a trade, would
have had nobody to start in a slot.

  ESPN   the weekly player pools espn_team_weeks.py lands: ESPN's projection
         (statSourceId 1) and actual points (statSourceId 0) for the week, already
         in the league's scoring, every position.
  Yahoo  actual points from the weekly player pool (stg_yahoo__player_pool);
         projections from fct_player_week_projection (ESPN's projection re-scored
         with the league's rules) through player_names.csv -- QB, RB, WR, TE. A
         kicker, defence or IDP, which ESPN does not project here, gets his own
         average of the weeks before (as matchups_export.py does).

Writes data/processed/{league}/{platform}_free_agents.json:

    {"league", "season", "weeks": {"7": [{"id", "name", "pos", "proj", "pts"}, ...]}}

Run after the other exports (it reads the team weeks and the warehouse).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import duckdb

from src.ingest.espn import RAW_DIR as ESPN_RAW
from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
PROCESSED = Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed")
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
NAMES = ROOT / "transform" / "seeds" / "player_names.csv"
TOP = 8
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
IDP = {"DE", "DT", "LB", "CB", "S", "DB", "DL", "D"}


def rostered(team_weeks: dict) -> dict[str, set[str]]:
    """{week: player ids on some roster} -- start or bench, any team."""
    out = defaultdict(set)
    for ps in team_weeks["grid"].values():
        for p in ps:
            for w, c in p["weeks"].items():
                if c.get("status") in ("start", "bench"):
                    out[w].add(str(p["id"]).split(".p.")[-1])
    return out


def espn_weeks(key: str, season: int, weeks: list[str], held: dict) -> dict:
    out = {}
    for w in weeks:
        f = ESPN_RAW / key / str(season) / "player_pool" / f"week{int(w):02d}.json"
        if not f.exists():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        data = data[0] if isinstance(data, list) and data else data
        rows = []
        for e in data.get("players") or []:
            p = e.get("player") or {}
            pos = ESPN_POS.get(p.get("defaultPositionId"))
            if not pos or str(p.get("id")) in held[w]:
                continue
            got = {(s.get("statSourceId"), s.get("statSplitTypeId")): s.get("appliedTotal")
                   for s in p.get("stats") or [] if s.get("scoringPeriodId") == int(w)}
            proj, pts = got.get((1, 1)), got.get((0, 1))
            if proj is None:
                continue
            rows.append({"id": str(p["id"]), "name": p.get("fullName"), "pos": pos,
                         "proj": round(float(proj), 2), "pts": round(float(pts or 0), 2)})
        out[w] = rows
    return out


def yahoo_weeks(key: str, weeks: list[str], held: dict) -> dict:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        pool = con.execute("""
            select week, cast(yahoo_player_id as varchar), player_name, display_position, fantasy_points
            from stg_yahoo__player_pool where week is not null""").fetchall()
        proj = {(int(w), g): x for w, g, x in con.execute(
            "select week, player_id, projected_points from fct_player_week_projection where league = ?", [key]).fetchall()}
    finally:
        con.close()
    with NAMES.open(encoding="utf-8", newline="") as fh:
        gsis = {r["yahoo_id"]: r["gsis_id"] for r in csv.DictReader(fh) if r["yahoo_id"] and r["gsis_id"]}
    by_week = defaultdict(dict)
    history = defaultdict(dict)                       # yahoo id -> week -> points
    for w, yid, name, shown, pts in pool:
        first = (shown or "").split(",")[0]
        pos = "DST" if first == "DEF" else "IDP" if first in IDP else first
        by_week[str(w)][yid] = (name, pos, float(pts or 0))
        history[yid][int(w)] = float(pts or 0)
    out = {}
    for w in weeks:
        rows = []
        for yid, (name, pos, pts) in by_week.get(w, {}).items():
            if yid in held[w]:
                continue
            p = proj.get((int(w), gsis.get(yid))) if pos in ("QB", "RB", "WR", "TE") else None
            if p is None:                                 # K / DST / IDP, or unprojected: his average so far
                before = [x for wk, x in history[yid].items() if wk < int(w)]
                p = sum(before) / len(before) if before else None
            if p is None:
                continue
            rows.append({"id": yid, "name": name, "pos": pos, "proj": round(float(p), 2), "pts": round(pts, 2)})
        out[w] = rows
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES))
    for key in selected(ap.parse_args().league):
        platform = LEAGUES[key]["platform"]
        tw = json.loads((PROCESSED / key / f"{platform}_team_weeks.json").read_text(encoding="utf-8"))
        weeks = [str(w) for w in tw["weeks"]]
        held = rostered(tw)
        rows = (espn_weeks(key, tw["season"], weeks, held) if platform == "espn"
                else yahoo_weeks(key, weeks, held))
        best = {}
        for w, xs in rows.items():
            per = defaultdict(list)
            for x in sorted(xs, key=lambda x: -x["proj"]):
                if len(per[x["pos"]]) < TOP:
                    per[x["pos"]].append(x)
            best[w] = [x for xs in per.values() for x in xs]
        out = PROCESSED / key / f"{platform}_free_agents.json"
        out.write_text(json.dumps({"league": key, "season": tw["season"], "weeks": best}, indent=1) + "\n",
                       encoding="utf-8")
        print(f"{key}: {len(best)} weeks, {sum(len(v) for v in best.values())} free agents -> {out}")


if __name__ == "__main__":
    main()
