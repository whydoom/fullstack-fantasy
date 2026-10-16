"""Join NFL plays to each league's fantasy rosters: top plays, team plays, red zone,
garbage time and defensive touchdowns, week by week.

    python plays_export.py                 # every league
    python plays_export.py --league adn

Reads fct_play_points (dbt: every play's fantasy points per league, see
transform/models/marts) and each league's roster grid (data/processed/{league}/
{platform}_team_weeks.json: who was on which roster each week, started or benched),
matched through transform/seeds/player_names.csv. Writes, per league,
data/processed/{league}/{platform}_plays.json:

    {"league", "season", "weeks": [...],
     "players": {week: {team_id: [player-week, ...]}},
     "team_plays": {week: [team-play, ...]},
     "defensive_tds": {week: [case, ...]}}

  player-week   one per player on a roster that week: name, position, status
                (start / bench), points, garbage-time points, red-zone carries /
                targets / touch share of his NFL team's red-zone opportunities, and
                his TOP_N plays by points (each: description, quarter, points,
                yards, TD, big play, vulture, garbage time, red zone, slot).
  team-play     a play that put points on a fantasy roster: the team, its players on
                the play (a QB and his receiver on one team is a stack), points to
                its starters and to its bench. Every play worth MIN_TEAM_PLAY points
                or a touchdown; the week's biggest is the first.
  defensive TD  the three cases the recap tells:
                self_made     a rostered player intercepted it, or forced and
                              recovered the fumble, and scored
                idp_and_dst   one team rostered both the defender who scored and that
                              NFL team's defence (IDP leagues)
                dst_returner  one team rostered a defence and the punt / kick
                              returner who scored for it

Game slot comes from dim_game. No API calls.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import duckdb

from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
NAMES = ROOT / "transform" / "seeds" / "player_names.csv"
TOP_N = 10
MIN_TEAM_PLAY = 8.0


def processed(league):
    return ROOT / "data" / "processed" / league


def load_names():
    """(by platform id, by gsis, dst team by platform id)."""
    by_platform, by_gsis, dst = {}, {}, {}
    with NAMES.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            for platform in ("espn", "yahoo"):
                pid = r[f"{platform}_id"]
                if not pid:
                    continue
                if r["position"] == "DST":
                    dst[(platform, pid)] = r["team"]
                elif r["gsis_id"]:
                    by_platform[(platform, pid)] = r["gsis_id"]
            if r["gsis_id"]:
                by_gsis[r["gsis_id"]] = r
    return by_platform, by_gsis, dst


def rosters(league, platform, by_platform, dst):
    """{week: {gsis or DST:<team>: (team_id, status, name, pos)}} from the roster grid."""
    tw = json.loads((processed(league) / f"{platform}_team_weeks.json").read_text(encoding="utf-8"))
    out = defaultdict(dict)
    for tid, players in tw["grid"].items():
        for p in players:
            pid = str(p["id"]).split(".p.")[-1]
            key = by_platform.get((platform, pid))
            if p["pos"] == "DST":
                key = f"DST:{dst.get((platform, pid), '')}"
            if not key:
                continue
            for wk, cell in p["weeks"].items():
                if cell.get("status") in ("start", "bench"):
                    out[wk][key] = (tid, cell["status"], p["name"], p["pos"])
    return tw["season"], [str(w) for w in tw["weeks"]], out


def main(league):
    platform = LEAGUES[league]["platform"]
    by_platform, by_gsis, dst = load_names()
    season, weeks, roster = rosters(league, platform, by_platform, dst)
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    cur = con.execute("""
        select p.*, g.slot
        from main.fct_play_points p
        join main.dim_game g using (game_id)
        where p.league = ? and p.season = ? and p.season_type = 'REG'
    """, [league, season])
    cols = [d[0] for d in cur.description]
    plays = [dict(zip(cols, row)) for row in cur.fetchall()]     # nulls stay None
    # every team's red-zone opportunities per week (all players, rostered or not)
    rz_team = {(r[0], int(r[1])): r[2] for r in con.execute("""
        select offense_team, week, sum(coalesce(target, 0) + coalesce(carry, 0))
        from main.fct_play_points
        where league = ? and season = ? and season_type = 'REG' and is_red_zone
        group by 1, 2""", [league, season]).fetchall()}

    def play_view(p):
        return {"game_id": p["game_id"], "play_id": int(p["play_id"]), "quarter": int(p["quarter"] or 0),
                "desc": p["play_description"], "points": round(float(p["points"]), 2),
                "yards": float(p["player_yards"] or 0), "td": bool(p["scored_td"]), "threw_td": bool(p["threw_td"]),
                "big_play": bool(p["is_big_play"]), "vulture": bool(p["is_vulture"]),
                "garbage_time": bool(p["is_garbage_time"]), "red_zone": bool(p["is_red_zone"]), "slot": p["slot"]}

    out_players, out_team_plays, out_def = {}, {}, {}
    for wk in weeks:
        week_plays = [p for p in plays if str(p["week"]) == wk]
        on_roster = roster.get(wk, {})

        # --- player-weeks ----------------------------------------------------------
        mine = defaultdict(list)
        for p in week_plays:
            if p["player_id"] in on_roster:
                mine[p["player_id"]].append(p)
        teams = defaultdict(list)
        for gsis, ps in mine.items():
            tid, status, name, pos = on_roster[gsis]
            rz = [p for p in ps if p["is_red_zone"]]
            # his NFL team: the offence on any play he was targeted or carried
            nfl_team = next((p["offense_team"] for p in ps if p["target"] or p["carry"]), None)
            rz_opps = rz_team.get((nfl_team, int(wk)), 0) if nfl_team else 0
            rz_touch = sum((p["target"] or 0) + (p["carry"] or 0) for p in rz)
            ranked = sorted(ps, key=lambda p: (-float(p["points"]), -float(p["player_yards"] or 0)))
            teams[tid].append({
                "player_id": gsis, "name": by_gsis.get(gsis, {}).get("display_name") or name,
                "pos": pos, "status": status,
                "points": round(sum(float(p["points"]) for p in ps), 2),
                "garbage_time_points": round(sum(float(p["points"]) for p in ps if p["is_garbage_time"]), 2),
                "red_zone": {"carries": int(sum(p["carry"] or 0 for p in rz)),
                             "targets": int(sum(p["target"] or 0 for p in rz)),
                             "team_opportunities": int(rz_opps),
                             "share": round(rz_touch / rz_opps, 3) if rz_opps else None},
                "big_plays": sum(1 for p in ps if p["is_big_play"]),
                "vultures": sum(1 for p in ps if p["is_vulture"]),
                "top_plays": [play_view(p) for p in ranked[:TOP_N] if float(p["points"]) != 0 or p["scored_td"]],
            })
        out_players[wk] = {tid: sorted(v, key=lambda r: -r["points"]) for tid, v in teams.items()}

        # --- team plays (stacks, bench, biggest) -------------------------------------
        by_play = defaultdict(list)
        for p in week_plays:
            if p["player_id"] in on_roster:
                by_play[(p["game_id"], p["play_id"])].append(p)
        team_plays = []
        for (gid, pid), ps in by_play.items():
            per_team = defaultdict(list)
            for p in ps:
                per_team[on_roster[p["player_id"]][0]].append(p)
            for tid, tps in per_team.items():
                started = sum(float(p["points"]) for p in tps if on_roster[p["player_id"]][1] == "start")
                bench = sum(float(p["points"]) for p in tps if on_roster[p["player_id"]][1] == "bench")
                td = any(p["scored_td"] or p["threw_td"] for p in tps)
                if max(started, bench) < MIN_TEAM_PLAY and not td:
                    continue
                team_plays.append({
                    "team_id": tid, "game_id": gid, "play_id": int(pid), "desc": tps[0]["play_description"],
                    "quarter": int(tps[0]["quarter"] or 0), "slot": tps[0]["slot"],
                    "started_points": round(started, 2), "bench_points": round(bench, 2),
                    "stack": len(tps) >= 2,
                    "players": [{"player_id": p["player_id"],
                                 "name": by_gsis.get(p["player_id"], {}).get("display_name") or on_roster[p["player_id"]][2],
                                 "status": on_roster[p["player_id"]][1], "points": round(float(p["points"]), 2)}
                                for p in sorted(tps, key=lambda p: -float(p["points"]))],
                    "garbage_time": any(p["is_garbage_time"] for p in tps),
                    "big_play": any(p["is_big_play"] for p in tps),
                })
        out_team_plays[wk] = sorted(team_plays, key=lambda t: (-t["started_points"], -t["bench_points"]))

        # --- defensive touchdowns ----------------------------------------------------
        cases = []
        dst_owner = {k[4:]: v[0] for k, v in on_roster.items() if k.startswith("DST:")}
        for p in week_plays:
            gsis = p["player_id"]
            if p["is_self_made_defensive_td"] and gsis in on_roster:
                cases.append({"case": "self_made", "team_id": on_roster[gsis][0], "player": on_roster[gsis][2],
                              "status": on_roster[gsis][1], "desc": p["play_description"]})
            if p["is_defensive_td"] and gsis in on_roster and dst_owner.get(p["defense_team"]) == on_roster[gsis][0]:
                cases.append({"case": "idp_and_dst", "team_id": on_roster[gsis][0], "player": on_roster[gsis][2],
                              "defense": p["defense_team"], "desc": p["play_description"]})
            if p["is_return_td"] and gsis in on_roster and dst_owner.get(p["touchdown_team"]) == on_roster[gsis][0]:
                cases.append({"case": "dst_returner", "team_id": on_roster[gsis][0], "player": on_roster[gsis][2],
                              "defense": p["touchdown_team"], "desc": p["play_description"]})
        out_def[wk] = cases

    out = processed(league) / f"{platform}_plays.json"
    out.write_text(json.dumps({"league": league, "season": season, "weeks": weeks, "top_n": TOP_N,
                               "players": out_players, "team_plays": out_team_plays,
                               "defensive_tds": out_def}, indent=1, default=str), encoding="utf-8")
    n_pw = sum(len(v) for w in out_players.values() for v in w.values())
    n_top = sum(len(r["top_plays"]) for w in out_players.values() for v in w.values() for r in v)
    print(f"{league}: {len(weeks)} weeks, {n_pw} player-weeks, {n_top} top plays, "
          f"{sum(len(v) for v in out_team_plays.values())} team plays, "
          f"{sum(len(v) for v in out_def.values())} defensive TD cases -> {out.name} "
          f"({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES), help="league key (default: every league)")
    for key in selected(ap.parse_args().league):
        main(key)
