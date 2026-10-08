"""Per-team weekly grid: every player the team rostered, by week.

For each week it needs two things:
  1. the full NFL player pool, to compute positional rank against everyone
     (not just rostered players) -- ESPN requires a sort alongside any limit
  2. the team's roster AS IT WAS that week, from the schedule's
     rosterForCurrentScoringPeriod, which carries lineupSlotId

Rows end up as the union of everyone who appeared on the roster in any week, so
a player shows a rank for every week he scored, with a status saying whether the
team had him and whether he started.

Raw pool responses are ~7MB each, so they are landed and reused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ingest.espn import (BASE, PROJECT_ROOT, EspnClient, current_season,
                             processed_dir)
from src.ingest.leagues import LEAGUES, selected

OUT_NAME = "espn_team_weeks.json"

ACTUAL, WEEKLY = 0, 1
BENCH_SLOTS = {20, 21}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

# ESPN lineupSlotId -> label. Subtotals group by SLOT rather than by player
# position: a FLEX pick can be an RB, WR or TE, so attributing it back to a
# position would let a position's "actual" exceed its own "optimal" whenever
# the best lineup flexed a different position. Grouping by slot keeps every
# subtotal honest and makes them sum to the team total.
SLOT_LABEL = {0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE",
              6: "TE", 7: "OP", 16: "DST", 17: "K", 23: "FLEX"}

# order the subtotal bands follow, matching how the player rows are sorted
SLOT_ORDER = ["QB", "RB", "WR", "TE", "K", "DST", "FLEX"]


def fetch_pool(client, season, week):
    """Full player pool for one week, landed to disk and reused.

    The pool is league-scoped -- points are under that league's scoring and
    onTeamId is that league's owner -- so each league lands its own.
    """
    dest = client.raw_dir / str(season) / "player_pool" / f"week{week:02d}.json"
    if dest.exists():
        return json.loads(dest.read_text())

    headers = dict(client.session.headers)
    headers["X-Fantasy-Filter"] = json.dumps({"players": {
        "limit": 1500,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }})
    r = client.session.get(
        f"{BASE}/seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "kona_player_info"), ("scoringPeriodId", week)],
        cookies=client.cookies, headers=headers, timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data))
    print(f"  landed pool week {week} ({len(r.content):,} bytes)")
    return data


def weekly_ranks(pool, week):
    """player_id -> (position, rank within position, points) for this week."""
    obj = pool[0] if isinstance(pool, list) and pool else pool
    rows = []
    for e in obj.get("players") or []:
        p = e.get("player") or {}
        wk = next((s for s in (p.get("stats") or [])
                   if s.get("scoringPeriodId") == week
                   and s.get("statSourceId") == ACTUAL
                   and s.get("statSplitTypeId") == WEEKLY), None)
        if not wk or wk.get("appliedTotal") is None:
            continue
        rows.append({
            "id": p.get("id"),
            "name": p.get("fullName"),
            "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
            "pts": round(wk["appliedTotal"], 2),
        })

    out = {}
    for pos in {r["pos"] for r in rows}:
        group = sorted([r for r in rows if r["pos"] == pos],
                       key=lambda r: (-r["pts"], r["name"] or ""))
        for i, r in enumerate(group, 1):
            out[r["id"]] = {"pos": pos, "rank": i, "pts": r["pts"],
                            "name": r["name"]}
    return out


def season_ranks(pool, season):
    """player_id -> season-to-date positional rank and total points.

    ESPN carries the running season total in the same stats array, as
    scoringPeriodId 0 with the season split (statSplitTypeId 0).
    """
    obj = pool[0] if isinstance(pool, list) and pool else pool
    rows = []
    for e in obj.get("players") or []:
        p = e.get("player") or {}
        tot = next((s for s in (p.get("stats") or [])
                    if s.get("scoringPeriodId") == 0
                    and s.get("statSourceId") == ACTUAL
                    and s.get("statSplitTypeId") == 0
                    and s.get("seasonId") == season), None)
        if not tot or tot.get("appliedTotal") is None:
            continue
        rows.append({"id": p.get("id"),
                     "name": p.get("fullName"),
                     "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
                     "pts": round(tot["appliedTotal"], 2)})

    out = {}
    for pos in {r["pos"] for r in rows}:
        group = sorted([r for r in rows if r["pos"] == pos],
                       key=lambda r: (-r["pts"], r["name"] or ""))
        for i, r in enumerate(group, 1):
            out[r["id"]] = {"pos": pos, "rank": i, "pts": r["pts"]}
    return out


def weekly_rosters(client, season, week):
    """Per team for this week: each player's status, points and slot eligibility,
    plus the score ESPN recorded for the matchup.

    Eligibility is needed to work out the best lineup that was available; a
    player can only fill a slot his eligibleSlots list contains.
    """
    data = client.get(
        f"seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "mBoxscore"), ("scoringPeriodId", week)],
    )
    obj = data[0] if isinstance(data, list) and data else data

    out = {}
    for game in obj.get("schedule") or []:
        if game.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            s = game.get(side) or {}
            tid = s.get("teamId")
            entries = (s.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
            if not tid or not entries:
                continue

            players = {}
            for e in entries:
                p = (e.get("playerPoolEntry") or {}).get("player") or {}
                wk = next((x for x in (p.get("stats") or [])
                           if x.get("scoringPeriodId") == week
                           and x.get("statSourceId") == ACTUAL
                           and x.get("statSplitTypeId") == WEEKLY), None)
                players[e["playerId"]] = {
                    "status": ("start" if e.get("lineupSlotId") not in BENCH_SLOTS
                               else "bench"),
                    # which slot he actually filled, needed for per-slot subtotals
                    "slot": e.get("lineupSlotId"),
                    # a rostered player with no actuals scored nothing, which is
                    # different from "no data" -- he still occupied a roster spot
                    "pts": round((wk or {}).get("appliedTotal") or 0.0, 2),
                    "slots": p.get("eligibleSlots") or [],
                }
            out[tid] = {"players": players, "actual": round(s.get("totalPoints") or 0.0, 2)}
    return out


def lineup_slots(client, season):
    """Starting slots and how many of each, read from league settings."""
    data = client.get(
        f"seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "mSettings")],
    )
    obj = data[0] if isinstance(data, list) and data else data
    counts = ((obj.get("settings") or {}).get("rosterSettings") or {}).get(
        "lineupSlotCounts") or {}
    return [(int(k), v) for k, v in counts.items()
            if v and int(k) not in BENCH_SLOTS]


def optimal_score(players, slots):
    """Best score the roster could have produced under the slot rules.

    Fills the most constrained slots first, then the flexible ones, taking the
    highest scorer still available at each step. With a single FLEX over
    RB/WR/TE this is exact: the flex simply takes the best leftover.
    """
    pool = {pid: d for pid, d in players.items()}
    # fewest eligible players first, so a scarce slot is not starved by a flex
    order = sorted(slots, key=lambda sc: sum(
        1 for d in pool.values() if sc[0] in d["slots"]))

    total, used = 0.0, set()
    lineup = []
    for slot_id, count in order:
        for _ in range(count):
            best, best_pts = None, None
            for pid, d in pool.items():
                if pid in used or slot_id not in d["slots"]:
                    continue
                if best_pts is None or d["pts"] > best_pts:
                    best, best_pts = pid, d["pts"]
            if best is None:
                continue
            used.add(best)
            total += best_pts
            lineup.append((slot_id, best, best_pts))
    return round(total, 2), lineup


def team_directory(client, season):
    data = client.get(
        f"seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "mTeam")],
    )
    obj = data[0] if isinstance(data, list) and data else data
    return {t["id"]: {"abbrev": t.get("abbrev") or f"T{t['id']}",
                      "name": t.get("name") or ""}
            for t in obj.get("teams") or []}


def latest_week(client, season):
    data = client.get(
        f"seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "mRoster")],
    )
    obj = data[0] if isinstance(data, list) and data else data
    best = 0
    for t in obj.get("teams") or []:
        for e in (t.get("roster") or {}).get("entries", []) or []:
            p = (e.get("playerPoolEntry") or {}).get("player") or {}
            for s in p.get("stats") or []:
                if (s.get("statSourceId") == ACTUAL
                        and s.get("statSplitTypeId") == WEEKLY
                        and (s.get("appliedTotal") or 0) != 0):
                    best = max(best, s.get("scoringPeriodId") or 0)
    return best


def main(league_key):
    client = EspnClient(league_key)
    season = current_season()
    last = latest_week(client, season)
    weeks = list(range(1, last + 1))
    print(f"season {season}, weeks 1-{last}")

    teams = team_directory(client, season)
    slots = lineup_slots(client, season)
    print(f"starting slots: {slots}")

    ranks_by_week, rosters_by_week = {}, {}
    for wk in weeks:
        print(f"week {wk}:")
        ranks_by_week[wk] = weekly_ranks(fetch_pool(client, season, wk), wk)
        rosters_by_week[wk] = weekly_rosters(client, season, wk)
        print(f"  {len(ranks_by_week[wk])} ranked players, "
              f"{len(rosters_by_week[wk])} rosters")

    season_tot = season_ranks(fetch_pool(client, season, weeks[-1]), season)
    print(f"season-to-date ranks for {len(season_tot)} players")

    # actual vs best-available, per team per week
    scores = {}
    for tid in teams:
        per_week = {}
        for wk in weeks:
            entry = rosters_by_week[wk].get(tid)
            if not entry:
                continue
            opt, lineup = optimal_score(entry["players"], slots)
            actual = entry["actual"]

            act_slot, opt_slot = {}, {}
            for d in entry["players"].values():
                if d["status"] != "start" or d.get("slot") is None:
                    continue
                act_slot[d["slot"]] = act_slot.get(d["slot"], 0.0) + d["pts"]
            for slot_id, _pid, pts in lineup:
                opt_slot[slot_id] = opt_slot.get(slot_id, 0.0) + pts

            by_slot = {}
            for slot_id, _count in slots:
                label = SLOT_LABEL.get(slot_id, f"S{slot_id}")
                by_slot[label] = {
                    "actual": round(act_slot.get(slot_id, 0.0), 2),
                    "optimal": round(opt_slot.get(slot_id, 0.0), 2),
                }

            per_week[str(wk)] = {
                "actual": actual,
                "optimal": opt,
                "left": round(max(opt - actual, 0.0), 2),
                "by_slot": by_slot,
            }

        wk_keys = [k for k in per_week if k != "season"]
        labels = sorted({lbl for k in wk_keys for lbl in per_week[k]["by_slot"]},
                        key=lambda l: (SLOT_ORDER.index(l)
                                       if l in SLOT_ORDER else 99, l))
        per_week["season"] = {
            "actual": round(sum(per_week[k]["actual"] for k in wk_keys), 2),
            "optimal": round(sum(per_week[k]["optimal"] for k in wk_keys), 2),
            "left": round(sum(per_week[k]["left"] for k in wk_keys), 2),
            "by_slot": {
                lbl: {
                    "actual": round(sum(
                        per_week[k]["by_slot"].get(lbl, {}).get("actual", 0.0)
                        for k in wk_keys), 2),
                    "optimal": round(sum(
                        per_week[k]["by_slot"].get(lbl, {}).get("optimal", 0.0)
                        for k in wk_keys), 2),
                } for lbl in labels
            },
        }
        scores[tid] = per_week

    # Union of every player each team held at any point
    grid = {}
    for tid in teams:
        seen = {}
        for wk in weeks:
            entry = rosters_by_week[wk].get(tid) or {}
            for pid, d in (entry.get("players") or {}).items():
                seen.setdefault(pid, {})[wk] = d["status"]

        players = []
        for pid, statuses in seen.items():
            meta = next((ranks_by_week[w][pid] for w in weeks
                         if pid in ranks_by_week[w]), None)
            cells = {}
            for wk in weeks:
                r = ranks_by_week[wk].get(pid)
                cells[str(wk)] = {
                    "rank": r["rank"] if r else None,
                    "pts": r["pts"] if r else None,
                    "status": statuses.get(wk, "off"),
                }
            st = season_tot.get(pid)
            players.append({
                "id": pid,
                "name": (meta or {}).get("name") or f"player {pid}",
                "pos": (meta or {}).get("pos") or (st or {}).get("pos") or "?",
                "season": {"rank": (st or {}).get("rank"),
                           "pts": (st or {}).get("pts")},
                "weeks": cells,
            })

        order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DST": 5, "?": 9}
        players.sort(key=lambda p: (order.get(p["pos"], 9), p["name"]))
        grid[tid] = players
        gaps = {k: v for k, v in scores.get(tid, {}).items() if k != "season"}
        left = sum(v["left"] for v in gaps.values())
        print(f"  team {tid} ({teams[tid]['abbrev']}): {len(players)} players, "
              f"{left:.1f} pts left on the bench over {len(gaps)} weeks")

    OUT = processed_dir(league_key) / OUT_NAME
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "league": league_key, "label": LEAGUES[league_key]["label"],
        "season": season, "weeks": weeks, "teams": teams,
        "slot_order": SLOT_ORDER,
        "grid": grid, "scores": scores,
    }, indent=2))
    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)}")


def cli(main):
    """Shared --league entry point: no flag runs every league in LEAGUES."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=list(LEAGUES),
                    help="league key from src/ingest/leagues.py (default: all)")
    for key in selected(ap.parse_args().league):
        print(f"\n=== {key} ===")
        main(key)


if __name__ == "__main__":
    cli(main)
