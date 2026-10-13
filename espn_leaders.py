"""Weekly and season leaderboards from the full ESPN player pool.

Supersedes espn_weekly_leaders.py, which only saw rostered players. The pool
payloads landed by espn_team_weeks.py cover every scoring player plus free
agents, so a top-24 list is NFL-wide rather than "the best of what the
league's teams happened to own".

Emits one view per week plus a season view, each with six position groups.
"""

from __future__ import annotations

import json

from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season, processed_dir
from src.ingest.leagues import LEAGUES
from espn_team_weeks import (ACTUAL, WEEKLY, POSITIONS, cli, fetch_pool,
                             latest_week, team_directory)

OUT_NAME = "espn_leaders.json"

# position -> how many rows to show
# Skill groups keep 25; how many a page shows is the site's layout decision (its
# leaders renderer cuts a stacked column to 12 + one header row + 12 = 25 rows).
# K and DST keep 64, i.e. every one who played: the site shows them as started
# lineups with a top-32 average above them and a count of unstarted players who
# beat the 3rd-best starter, which needs everyone above that bar.
# Every group now carries 75 (K and DST: everyone who played): the site finds the
# best free agent at each position each week, which can rank well below 25.
GROUPS = [("RB", 75), ("WR", 75), ("QB", 75), ("TE", 75), ("K", 75), ("DST", 75)]


def extract(pool, season, week=None):
    """Ranked rows per position. week=None means season-to-date totals."""
    obj = pool[0] if isinstance(pool, list) and pool else pool
    rows = []
    for e in obj.get("players") or []:
        p = e.get("player") or {}
        if week is None:
            stat = next((s for s in (p.get("stats") or [])
                         if s.get("scoringPeriodId") == 0
                         and s.get("statSourceId") == ACTUAL
                         and s.get("statSplitTypeId") == 0
                         and s.get("seasonId") == season), None)
        else:
            stat = next((s for s in (p.get("stats") or [])
                         if s.get("scoringPeriodId") == week
                         and s.get("statSourceId") == ACTUAL
                         and s.get("statSplitTypeId") == WEEKLY), None)
        if not stat or stat.get("appliedTotal") is None:
            continue
        rows.append({
            "name": p.get("fullName"),
            "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
            "pts": round(stat["appliedTotal"], 2),
            # 0 means nobody owns him -- a free agent, which is worth seeing
            "team_id": e.get("onTeamId") or 0,
        })

    out = []
    for pos, limit in GROUPS:
        group = sorted([r for r in rows if r["pos"] == pos],
                       key=lambda r: (-r["pts"], r["name"] or ""))
        out.append({"pos": pos, "players": group[:limit]})
    return out


def main(league_key):
    client = EspnClient(league_key)
    season = current_season()
    last = latest_week(client, season)
    weeks = list(range(1, last + 1))
    print(f"season {season}, weeks 1-{last}")

    teams = team_directory(client, season)
    teams["0"] = {"abbrev": "FA", "name": "Free agent"}

    views = {}
    pools = {}
    for wk in weeks:
        pools[wk] = fetch_pool(client, season, wk)
        views[str(wk)] = extract(pools[wk], season, wk)
        counts = {g["pos"]: len(g["players"]) for g in views[str(wk)]}
        print(f"  week {wk}: {counts}")

    # Season totals live in the most recent pool
    views["season"] = extract(pools[weeks[-1]], season, week=None)
    print(f"  season: {{g['pos']: len(g['players']) for g in views['season']}}"
          .replace("{g['pos']: len(g['players']) for g in views['season']}",
                   str({g["pos"]: len(g["players"]) for g in views["season"]})))

    OUT = processed_dir(league_key) / OUT_NAME
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "league": league_key, "label": LEAGUES[league_key]["label"],
        "season": season, "weeks": weeks, "teams": teams, "views": views,
    }, indent=2))
    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    cli(main)
