"""Export a Yahoo league's dbt marts as the JSON the site's renderers read.

    python yahoo_site_export.py            # every Yahoo league in src/ingest/leagues.py
    python yahoo_site_export.py --league adn

Writes data/processed/{league}/yahoo_{leaders,team_weeks,luck}.json in the same
shapes the ESPN extractors write (espn_leaders.py, espn_team_weeks.py,
espn_luck.py), so jimoody.com's render_*.py scripts draw both platforms with the
same code. FAAB has its own per-league script (faab_adn.py).

This is a presentation layer, not a model: every number here was computed and
tested in dbt (fct_roster_slot, fct_matchup, fct_player_rank,
fct_team_week_allplay, dim_team_season). What happens here is reshaping into the
renderers' contract, plus two things that are presentation choices rather than
facts:

  * Labels are conformed to the site's vocabulary, which predates Yahoo:
    DEF -> DST and W/R/T -> FLEX (ESPN's names for the same things), and Yahoo's
    individual-defender slot D -> IDP, a group ESPN leagues do not have.
  * Team abbreviations. Yahoo has none; they are made from manager nicknames.

The best-lineup calculation is espn_team_weeks.optimal_score, imported rather than
copied, so both platforms' "left on the bench" numbers come from one algorithm.

Only FINAL weeks are exported. A week still in progress would show half-played
lineups as points "left on the bench".

Season-to-date player totals (the leaders' Season view, the grid's Season column)
come from Yahoo's season stats, which include any week in progress -- Yahoo offers
no "through week N" season total. Exported mid-week, a player's Season cell would
exceed the sum of the weekly cells beside it. Between Monday night's last game and
Thursday's kickoff the two agree, so that is when to export; run any other time,
this script still writes the files but warns, naming the live week.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

import duckdb
from zoneinfo import ZoneInfo

from espn_team_weeks import optimal_score
from src.ingest.discover import MANIFEST_PATH
from src.ingest.leagues import LEAGUES, keys, selected
from src.load.yahoo_raw import WAREHOUSE_PATH

PROJECT_ROOT = WAREHOUSE_PATH.parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed"

# Yahoo label -> the site's label. Anything not listed passes through unchanged.
LABEL = {"DEF": "DST", "W/R/T": "FLEX", "D": "IDP"}
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DST", "IDP"]
SLOT_ORDER = ["QB", "RB", "WR", "TE", "K", "DST", "IDP", "FLEX"]
LEADERS_ORDER = ["RB", "WR", "QB", "TE", "K", "DST", "IDP"]   # espn_leaders' order
LEADERS_DEPTH = 25
_ET = ZoneInfo("America/New_York")   # Yahoo week windows are US Eastern dates
CLOSE_MARGIN = 5.0   # must match the close_game_margin var fct_team_week_allplay uses

# Yahoo display positions that are individual defenders, i.e. play the D slot.
OFFENSE_AND_UNITS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def label(position: str) -> str:
    return LABEL.get(position, position)


def position_group(display_position: str) -> str:
    """Yahoo position group a player ranks in: his primary listed position, with
    every individual defender (LB, DE, CB, S, ...) grouped as D. A two-way player
    listed 'WR,CB' ranks as a WR."""
    primary = display_position.split(",")[0]
    return primary if primary in OFFENSE_AND_UNITS else "D"


def resolve_league_key(name: str) -> str:
    """Latest season's league_key for a Yahoo league name, from discovery."""
    rows = [lg for lg in json.loads(MANIFEST_PATH.read_text()) if lg.get("name") == name]
    if not rows:
        raise SystemExit(f"No Yahoo league named {name!r} in {MANIFEST_PATH}.")
    return max(rows, key=lambda lg: int(lg["season"]))["league_key"]


def abbreviations(nicknames: dict[int, str]) -> dict[int, str]:
    """Four-letter team labels from manager nicknames, made unique."""
    out, used = {}, set()
    for tid in sorted(nicknames):
        base = (re.sub(r"[^A-Za-z]", "", nicknames[tid]).upper() or f"T{tid}")[:4]
        ab, n = base, 2
        while ab in used:
            ab, n = f"{base[:3]}{n}", n + 1
        used.add(ab)
        out[tid] = ab
    return out


def rows(con, sql: str, *params) -> list[dict]:
    cur = con.execute(sql, list(params))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(league: str) -> None:
    cfg = LEAGUES[league]
    lk = resolve_league_key(cfg["yahoo_league_name"])
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    team_rows = rows(con, "select team_id, team_name, manager_nickname, season "
                          "from dim_team_season where league_key = ? order by team_id", lk)
    if not team_rows:
        raise SystemExit(f"No teams for {lk} in the warehouse. Load and build first.")
    season = team_rows[0]["season"]
    abbrev = abbreviations({t["team_id"]: t["manager_nickname"] for t in team_rows})
    teams = {str(t["team_id"]): {"abbrev": abbrev[t["team_id"]], "name": t["team_name"]}
             for t in team_rows}

    weeks = [r["week"] for r in rows(con, """
        select week from fct_matchup where league_key = ?
        group by week having bool_and(is_final) order by week""", lk)]
    if not weeks:
        raise SystemExit(f"{lk}: no final weeks yet.")
    print(f"{league} ({lk}): season {season}, final weeks {weeks}, {len(teams)} teams")
    live = [r["week"] for r in rows(con, """select distinct week from stg_yahoo__matchup_teams
                                            where league_key = ? and matchup_status = 'midevent'""", lk)]
    if live:
        print(f"  WARNING: week {live[0]} is in progress. Season totals include its points "
              f"so far, so Season cells will not equal the sum of weeks {weeks[0]}-{weeks[-1]}. "
              "Re-export between Monday night and Thursday kickoff for a consistent page.")

    # ---- shared lookups -----------------------------------------------------
    ranks = defaultdict(dict)   # period_key -> (position_group, player_key) -> rank row
    for r in rows(con, """select period_key, position_group, player_key, player_name,
                                 fantasy_points, position_rank
                          from fct_player_rank where league_key = ?""", lk):
        ranks[r["period_key"]][(r["position_group"], r["player_key"])] = r

    slots_by_week = defaultdict(lambda: defaultdict(list))   # week -> team -> slot rows
    for r in rows(con, """select s.week, s.team_id, s.player_key, s.selected_position,
                                 s.eligible_positions, s.is_starter, s.fantasy_points,
                                 p.player_name, p.display_position
                          from fct_roster_slot s
                          join stg_yahoo__roster_slots p using (league_key, week, player_key)
                          where s.league_key = ?""", lk):
        slots_by_week[r["week"]][r["team_id"]].append(r)
    latest_roster_week = max(slots_by_week)

    lineup_slots = [(r["position"], r["slot_count"]) for r in rows(con, """
        select position, slot_count from stg_yahoo__roster_positions
        where league_key = ? and not is_reserve_slot""", lk)]

    out_dir = PROCESSED / league
    out_dir.mkdir(parents=True, exist_ok=True)
    header = {"league": league, "label": cfg["label"], "season": season, "weeks": weeks}

    # ---- leaders ------------------------------------------------------------
    def owners(week: int) -> dict[str, int]:
        return {r["player_key"]: tid for tid, rs in slots_by_week[week].items() for r in rs}

    views = {}
    for period, week in [(f"W{w:02d}", w) for w in weeks] + [("SEASON", latest_roster_week)]:
        own = owners(week)
        groups = []
        for pos in LEADERS_ORDER:
            yahoo_pos = next((k for k, v in LABEL.items() if v == pos), pos)
            top = sorted((r for (g, _), r in ranks[period].items() if g == yahoo_pos),
                         key=lambda r: r["position_rank"])[:LEADERS_DEPTH]
            groups.append({"pos": pos, "players": [
                {"name": r["player_name"], "pos": pos,
                 "pts": float(r["fantasy_points"]),
                 "team_id": own.get(r["player_key"], 0)} for r in top]})
        views["season" if period == "SEASON" else str(week)] = groups
    leaders_teams = dict(teams, **{"0": {"abbrev": "FA", "name": "Free agent"}})
    (out_dir / "yahoo_leaders.json").write_text(json.dumps(
        dict(header, teams=leaders_teams, views=views), indent=2), encoding="utf-8")

    # ---- team weeks ---------------------------------------------------------
    actual = {(r["week"], r["team_id"]): float(r["team_points"]) for r in rows(con,
              "select week, team_id, team_points from fct_matchup where league_key = ?", lk)}
    grid, scores = {}, {}
    for tid in sorted(int(t) for t in teams):
        per_week = {}
        for w in weeks:
            roster = slots_by_week[w][tid]
            players = {r["player_key"]: {"pts": float(r["fantasy_points"]),
                                         "slots": list(r["eligible_positions"])}
                       for r in roster}
            opt, lineup = optimal_score(players, lineup_slots)
            act_slot, opt_slot = defaultdict(float), defaultdict(float)
            for r in roster:
                if r["is_starter"]:
                    act_slot[label(r["selected_position"])] += float(r["fantasy_points"])
            for slot, _pk, pts in lineup:
                opt_slot[label(slot)] += pts
            act = actual[(w, tid)]
            per_week[str(w)] = {
                "actual": act, "optimal": opt, "left": round(max(opt - act, 0.0), 2),
                "by_slot": {label(s): {"actual": round(act_slot[label(s)], 2),
                                       "optimal": round(opt_slot[label(s)], 2)}
                            for s, _c in lineup_slots}}
        labels = [l for l in SLOT_ORDER if l in per_week[str(weeks[0])]["by_slot"]]
        per_week["season"] = {
            "actual": round(sum(per_week[str(w)]["actual"] for w in weeks), 2),
            "optimal": round(sum(per_week[str(w)]["optimal"] for w in weeks), 2),
            "left": round(sum(per_week[str(w)]["left"] for w in weeks), 2),
            "by_slot": {l: {k: round(sum(per_week[str(w)]["by_slot"][l][k] for w in weeks), 2)
                            for k in ("actual", "optimal")} for l in labels}}
        scores[str(tid)] = per_week

        held = {}   # player_key -> {"meta": row, "status": {week: status}}
        for w in weeks:
            for r in slots_by_week[w][tid]:
                h = held.setdefault(r["player_key"], {"meta": r, "status": {}})
                h["status"][w] = "start" if r["is_starter"] else "bench"
        players_out = []
        for pk, h in held.items():
            group = position_group(h["meta"]["display_position"])
            def cell(period):
                r = ranks[period].get((group, pk))
                return (r["position_rank"], float(r["fantasy_points"])) if r else (None, None)
            cells = {}
            for w in weeks:
                rank, pts = cell(f"W{w:02d}")
                cells[str(w)] = {"rank": rank, "pts": pts, "status": h["status"].get(w, "off")}
            s_rank, s_pts = cell("SEASON")
            players_out.append({"id": pk, "name": h["meta"]["player_name"], "pos": label(group),
                                "season": {"rank": s_rank, "pts": s_pts}, "weeks": cells})
        players_out.sort(key=lambda p: (POSITION_ORDER.index(p["pos"]), p["name"]))
        grid[str(tid)] = players_out
    (out_dir / "yahoo_team_weeks.json").write_text(json.dumps(
        dict(header, teams=teams, slot_order=SLOT_ORDER, grid=grid, scores=scores),
        indent=2), encoding="utf-8")

    # ---- luck ---------------------------------------------------------------
    luck = []
    for r in rows(con, """
        select a.team_id,
               count(*) filter (where m.result = 'W')                    as w,
               count(*) filter (where m.result = 'L')                    as l,
               count(*) filter (where m.result = 'T')                    as t,
               sum(a.team_points)                                        as pf,
               sum(a.expected_wins)                                      as ev,
               count(*) filter (where a.beat_median)                     as med_w,
               count(*) filter (where not a.beat_median)                 as med_l,
               sum(a.allplay_wins) as ap_w, sum(a.allplay_losses) as ap_l,
               sum(a.allplay_ties) as ap_t,
               count(*) filter (where a.is_close_game and m.result = 'W') as cw,
               count(*) filter (where a.is_close_game and m.result = 'L') as cl,
               count(*) filter (where a.is_close_game and m.result = 'T') as ct
        from fct_team_week_allplay a
        join fct_matchup m using (league_key, week, team_id)
        where a.league_key = ?
        group by a.team_id""", lk):
        tid = r["team_id"]
        rec = lambda w, l, t: f"{w}-{l}" + (f"-{t}" if t else "")
        close_n = r["cw"] + r["cl"] + r["ct"]
        ap_n = r["ap_w"] + r["ap_l"] + r["ap_t"]
        luck.append({
            "team_id": tid, "abbrev": abbrev[tid], "name": teams[str(tid)]["name"],
            "record": rec(r["w"], r["l"], r["t"]), "wins": r["w"], "ties": r["t"],
            "played": r["w"] + r["l"] + r["t"], "pf": round(float(r["pf"]), 1),
            "ev": round(float(r["ev"]), 2), "diff": round(r["w"] - float(r["ev"]), 2),
            "median": f"{r['med_w']}-{r['med_l']}",
            "everyone": rec(r["ap_w"], r["ap_l"], r["ap_t"]),
            "everyone_pct": round((r["ap_w"] + 0.5 * r["ap_t"]) / max(ap_n, 1), 3),
            "close": rec(r["cw"], r["cl"], r["ct"]) if close_n else "—",
            "close_n": close_n,
        })
    luck.sort(key=lambda r: -r["ev"])
    (out_dir / "yahoo_luck.json").write_text(json.dumps(
        dict(header, n_teams=len(teams), close_margin=CLOSE_MARGIN, rows=luck),
        indent=2), encoding="utf-8")

    con.close()
    print(f"  wrote yahoo_leaders.json ({len(views)} views), yahoo_team_weeks.json "
          f"({sum(len(g) for g in grid.values())} player rows), yahoo_luck.json "
          f"({len(luck)} teams) to {out_dir.relative_to(PROJECT_ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=keys("yahoo"),
                    help="Yahoo league key from src/ingest/leagues.py (default: all)")
    for league in selected(ap.parse_args().league, platform="yahoo"):
        export(league)


if __name__ == "__main__":
    main()
