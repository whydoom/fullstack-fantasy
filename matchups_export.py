"""How each fantasy matchup moved, slate by slate, against its projection; and the
state of every matchup going into Monday night.

    python matchups_export.py                 # every league
    python matchups_export.py --league adn

Writes data/processed/{league}/{platform}_matchup_trend.json:

    {"league", "season", "weeks": [...], "games": {week: [game, ...]}}

  game   two teams, t1 the winner. For each:
           projected   its starters' projections summed: ESPN's pre-game projection
                       scored with the league's weights (fct_player_week_projection)
                       for QB / RB / WR / TE; for kickers, defences and IDPs, which
                       have no projection here, the player's own average in earlier
                       weeks (the league's average at the position in week 1)
           final       the official score
         spread        t1's projected total minus t2's: > 0 means t1 was favoured
         slates        in kickoff order (thursday, sunday_day, sunday_night, monday,
                       other_day for Wednesday / Friday / Saturday): for each team its
                       starters' actual and projected points in that slate and the
                       difference (over), and after the slate the projected final
                       margin -- points so far plus projections still to play
         best_slate /  per team, the slate its players beat / missed their
         worst_slate   projections by most
         monday        going into Monday night: each team's points, who it had left
                       (name, projection), the margin, what Monday produced, and
                       whether the lead changed hands

Actual points are the platform's official ones (the roster grid); projections are
approximate until league_scoring holds each league's exact rules. A starter on bye
sits outside the slates (0 projected, 0 scored). No API calls.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import duckdb

from src.ingest.leagues import LEAGUES, selected

ROOT = Path(__file__).resolve().parent
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
NAMES = ROOT / "transform" / "seeds" / "player_names.csv"
PROJECTED_POSITIONS = {"QB", "RB", "WR", "TE"}


def processed(league):
    return Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed") / league


def main(league):
    platform = LEAGUES[league]["platform"]
    tw = json.loads((processed(league) / f"{platform}_team_weeks.json").read_text(encoding="utf-8"))
    luck = json.loads((processed(league) / f"{platform}_luck.json").read_text(encoding="utf-8"))
    season, weeks = tw["season"], [str(w) for w in tw["weeks"]]
    con = duckdb.connect(str(WAREHOUSE), read_only=True)

    # ---- who is who: platform id -> gsis, defences -> NFL team -------------------
    gsis_of, dst_team = {}, {}
    with NAMES.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            pid = r[f"{platform}_id"]
            if not pid:
                continue
            if r["position"] == "DST":
                dst_team[pid] = r["team"]
            elif r["gsis_id"]:
                gsis_of[pid] = r["gsis_id"]

    # ---- each player's NFL team per week, and each team's game slate --------------
    team_by_week = defaultdict(dict)
    for gsis, wk, team in con.execute("""select player_id, week, team_abbr from main.stg_nflverse__player_stats_weekly
                                          where season = ? and season_type = 'REG'""", [season]).fetchall():
        team_by_week[gsis][int(wk)] = team

    def nfl_team(gsis, wk):
        teams = team_by_week.get(gsis) or {}
        if wk in teams:
            return teams[wk]
        if not teams:
            return None
        return teams[min(teams, key=lambda w: (abs(w - wk), -w))]    # nearest week, later first

    slate_of, slate_order = {}, defaultdict(dict)
    for wk, slate, gday, kick, home, away in con.execute("""select week, slate, game_date, kickoff_time_et,
            home_team_abbr, away_team_abbr from main.dim_game where season = ? and game_type = 'REG'""", [season]).fetchall():
        for team in (home, away):
            slate_of[(int(wk), team)] = slate
        first = slate_order[int(wk)].get(slate)
        slate_order[int(wk)][slate] = min(first, (gday, kick)) if first else (gday, kick)

    # ---- projections ------------------------------------------------------------------
    try:
        espn = {(g, int(w)): p for g, w, p in con.execute("""select player_id, week, projected_points
            from main.fct_player_week_projection where league = ? and season = ?""", [league, season]).fetchall()}
    except duckdb.CatalogException:
        espn = {}

    # official points per player per week (start or bench), for the averages
    history = defaultdict(dict)          # player key -> {week: pts}
    pos_week = defaultdict(list)         # (pos, week) -> started pts, for week-1 fallbacks
    players = {}
    for tid, ps in tw["grid"].items():
        for p in ps:
            pid = str(p["id"]).split(".p.")[-1]
            key = f"DST:{dst_team.get(pid, pid)}" if p["pos"] == "DST" else gsis_of.get(pid, f"{platform}:{pid}")
            players[(tid, p["id"])] = (key, p)
            for wk, c in p["weeks"].items():
                if c.get("status") in ("start", "bench") and c.get("pts") is not None:
                    history[key][int(wk)] = float(c["pts"])
                    if c["status"] == "start":
                        pos_week[(p["pos"], int(wk))].append(float(c["pts"]))

    def projection(key, pos, wk):
        """(points, source) for one starter."""
        if pos in PROJECTED_POSITIONS and (key, wk) in espn:
            return float(espn[(key, wk)]), "espn"
        earlier = [v for w, v in history.get(key, {}).items() if w < wk]
        if earlier:
            return sum(earlier) / len(earlier), "average"
        same = pos_week.get((pos, wk)) or [0.0]
        return sum(same) / len(same), "position_average"

    # ---- games: pair teams on each week's scores ----------------------------------------
    side = {str(r["team_id"]): {str(x["week"]): x for x in r.get("weekly", [])} for r in luck["rows"]}

    out_games = {}
    for wk in weeks:
        w = int(wk)
        order = sorted(slate_order[w], key=lambda s: slate_order[w][s])

        # every starter: (team, name, pos, actual, projected, source, slate)
        starters = defaultdict(list)
        for (tid, _), (key, p) in players.items():
            c = p["weeks"].get(wk) or {}
            if c.get("status") != "start":
                continue
            team = key[4:] if key.startswith("DST:") else nfl_team(key, w)
            slate = slate_of.get((w, team))
            proj, source = projection(key, p["pos"], w) if slate else (0.0, "bye")
            starters[tid].append({"name": p["name"], "pos": p["pos"], "actual": float(c.get("pts") or 0),
                                  "projected": round(proj, 2), "source": source, "slate": slate or "bye"})

        left = {t for t in side if wk in side[t]}
        games = []
        for a in sorted(left, key=lambda t: -side[t][wk]["pts"]):
            if a not in left:
                continue
            ga = side[a][wk]
            b = next((t for t in sorted(left) if t != a and abs(side[t][wk]["pts"] - ga["opp"]) < 0.01
                      and abs(side[t][wk]["opp"] - ga["pts"]) < 0.01), None)
            if b is None:
                continue
            left -= {a, b}
            t1, t2 = (a, b) if ga["result"] != "L" else (b, a)
            games.append(game_view(wk, t1, t2, side, starters, order))
        out_games[wk] = games

    out = processed(league) / f"{platform}_matchup_trend.json"
    out.write_text(json.dumps({"league": league, "season": season, "weeks": weeks, "games": out_games},
                              indent=1, default=str), encoding="utf-8")
    sources = defaultdict(int)
    for gs in out_games.values():
        for g in gs:
            for t in g["teams"]:
                for s in t["starters"]:
                    sources[s["source"]] += 1
    print(f"{league}: {sum(len(v) for v in out_games.values())} games over {len(weeks)} weeks -> {out.name} "
          f"(starter projections: {dict(sources)})")


def game_view(wk, t1, t2, side, starters, order):
    """One matchup: spread, the trend slate by slate, best / worst slates, Monday."""
    def slate_sum(tid, slate, field):
        return round(sum(s[field] for s in starters[tid] if s["slate"] == slate), 2)

    proj = {t: round(sum(s["projected"] for s in starters[t]), 2) for t in (t1, t2)}
    final = {t1: side[t1][wk]["pts"], t2: side[t2][wk]["pts"]}
    slates, done = [], {t1: 0.0, t2: 0.0}
    remaining = dict(proj)
    for slate in order:
        row = {"slate": slate}
        for t in (t1, t2):
            actual, projected = slate_sum(t, slate, "actual"), slate_sum(t, slate, "projected")
            done[t] += actual
            remaining[t] -= projected
            row[t] = {"actual": actual, "projected": projected, "over": round(actual - projected, 2),
                      "players": [s["name"] for s in starters[t] if s["slate"] == slate]}
        row["projected_margin"] = round((done[t1] + remaining[t1]) - (done[t2] + remaining[t2]), 2)
        slates.append(row)

    def extreme(t, best):
        played = [r for r in slates if r[t]["players"]]
        if not played:
            return None
        r = (max if best else min)(played, key=lambda r: r[t]["over"])
        return {"slate": r["slate"], "over": r[t]["over"]}

    mon = next((i for i, r in enumerate(slates) if r["slate"] == "monday"), None)
    monday = None
    if mon is not None and (slates[mon][t1]["players"] or slates[mon][t2]["players"]):
        before = {t: round(sum(r[t]["actual"] for r in slates[:mon]), 2) for t in (t1, t2)}
        leader = t1 if before[t1] >= before[t2] else t2
        monday = {
            "entering": before,
            "margin": round(abs(before[t1] - before[t2]), 2),
            "leader": leader,
            "left": {t: [{"name": s["name"], "pos": s["pos"], "projected": s["projected"], "actual": s["actual"]}
                         for s in starters[t] if s["slate"] == "monday"] for t in (t1, t2)},
            "monday_points": {t: slates[mon][t]["actual"] for t in (t1, t2)},
            "lead_changed": leader != t1,
        }
    return {
        "teams": [{"team_id": t, "projected": proj[t], "final": final[t],
                   "best_slate": extreme(t, True), "worst_slate": extreme(t, False),
                   "starters": starters[t]} for t in (t1, t2)],
        "spread": round(proj[t1] - proj[t2], 2),
        "margin": round(final[t1] - final[t2], 2),
        "slates": slates,
        "monday": monday,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES), help="league key (default: every league)")
    for key in selected(ap.parse_args().league):
        main(key)
