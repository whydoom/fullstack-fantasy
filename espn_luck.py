"""Schedule luck: what each team's record would be if the draw were neutral.

Three measures, all from the same weekly scores:

  expected wins   the all-play record. If you score rank r out of n, you would
                  have beaten (n - r) of the other (n - 1) teams, so that week
                  is worth (n - r)/(n - 1) of a win. Ties count half.

  vs median       whether you cleared the week's median score. With an even
                  team count this is identical to finishing in the top half --
                  a more readable restatement of rank, not new information.

  close games     record in games decided by <= 5 points, where the result is
                  closest to a coin flip.

Sanity check that runs on every build: each week's expected wins must sum to
exactly half the teams, because that is how many real wins a week hands out.
"""

from __future__ import annotations

import json
from statistics import median

from src.ingest.espn import PROJECT_ROOT, EspnClient, current_season, processed_dir
from src.ingest.leagues import LEAGUES
from espn_team_weeks import cli

OUT_NAME = "espn_luck.json"

CLOSE_MARGIN = 5.0


def fetch_season(client, season):
    """One call gets every matchup's score for the whole season."""
    data = client.get(
        f"seasons/{season}/segments/0/leagues/{client.league_id}",
        params=[("view", "mMatchupScore"), ("view", "mTeam"), ("view", "mSettings")],
    )
    return data[0] if isinstance(data, list) and data else data


def collect_games(obj, reg_weeks):
    """Completed regular-season games as (week, team, score, opp, opp_score)."""
    games = []
    for g in obj.get("schedule") or []:
        wk = g.get("matchupPeriodId")
        if not wk or wk > reg_weeks:
            continue
        if g.get("winner") in (None, "", "UNDECIDED"):
            continue
        h, a = g.get("home") or {}, g.get("away") or {}
        if not h.get("teamId") or not a.get("teamId"):
            continue          # bye or a malformed row
        hs = round(h.get("totalPoints") or 0.0, 2)
        as_ = round(a.get("totalPoints") or 0.0, 2)
        games.append((wk, h["teamId"], hs, a["teamId"], as_))
        games.append((wk, a["teamId"], as_, h["teamId"], hs))
    return games


def main(league_key):
    client = EspnClient(league_key)
    season = current_season()
    obj = fetch_season(client, season)

    settings = obj.get("settings") or {}
    reg_weeks = (settings.get("scheduleSettings") or {}).get("matchupPeriodCount") or 14
    teams = {t["id"]: {"abbrev": t.get("abbrev") or f"T{t['id']}",
                       "name": t.get("name") or ""}
             for t in obj.get("teams") or []}
    n = len(teams)

    games = collect_games(obj, reg_weeks)
    weeks = sorted({g[0] for g in games})
    print(f"season {season}: {n} teams, {len(weeks)} completed weeks {weeks}")

    stats = {tid: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "ev": 0.0,
                   "med_w": 0, "med_l": 0,
                   "ap_w": 0, "ap_l": 0, "ap_t": 0,
                   "close_w": 0, "close_l": 0, "close_t": 0}
             for tid in teams}

    checked = []
    for wk in weeks:
        wk_games = [g for g in games if g[0] == wk]
        scores = {g[1]: g[2] for g in wk_games}
        if len(scores) != n:
            print(f"  week {wk}: only {len(scores)} of {n} teams -- skipped")
            continue

        med = median(scores.values())
        ev_sum = 0.0

        for _, tid, pts, opp, opp_pts in wk_games:
            s = stats[tid]
            s["pf"] += pts

            # head to head
            if pts > opp_pts:
                s["w"] += 1
            elif pts < opp_pts:
                s["l"] += 1
            else:
                s["t"] += 1

            # all-play: beaten + half of tied, over the other n-1 teams
            beaten = sum(1 for o, v in scores.items() if o != tid and v < pts)
            tied = sum(1 for o, v in scores.items() if o != tid and v == pts)
            ev = (beaten + 0.5 * tied) / (n - 1)
            s["ev"] += ev
            ev_sum += ev

            # the same thing as a record: n - 1 notional games every week
            s["ap_w"] += beaten
            s["ap_t"] += tied
            s["ap_l"] += (n - 1) - beaten - tied

            # against the week's median
            if pts > med:
                s["med_w"] += 1
            else:
                s["med_l"] += 1

            # close game
            if abs(pts - opp_pts) <= CLOSE_MARGIN:
                if pts > opp_pts:
                    s["close_w"] += 1
                elif pts < opp_pts:
                    s["close_l"] += 1
                else:
                    s["close_t"] += 1

        # a week hands out exactly n/2 wins, so the expected wins must agree
        if abs(ev_sum - n / 2) > 1e-6:
            raise AssertionError(
                f"week {wk}: expected wins sum to {ev_sum:.4f}, should be {n/2}")
        checked.append((wk, ev_sum))

    print(f"expected-wins check passed for {len(checked)} weeks: "
          + ", ".join(f"W{w}={v:.4f}" for w, v in checked) + f" (each = n/2 = {n/2})")

    rows = []
    for tid, s in stats.items():
        played = s["w"] + s["l"] + s["t"]
        rows.append({
            "team_id": tid,
            "abbrev": teams[tid]["abbrev"],
            "name": teams[tid]["name"],
            "record": f"{s['w']}-{s['l']}" + (f"-{s['t']}" if s["t"] else ""),
            "wins": s["w"],
            "ties": s["t"],
            "played": played,
            "pf": round(s["pf"], 1),
            "ev": round(s["ev"], 2),
            "diff": round(s["w"] - s["ev"], 2),
            "median": f"{s['med_w']}-{s['med_l']}",
            "everyone": (f"{s['ap_w']}-{s['ap_l']}"
                         + (f"-{s['ap_t']}" if s["ap_t"] else "")),
            "everyone_pct": round(
                (s["ap_w"] + 0.5 * s["ap_t"]) / max(s["ap_w"] + s["ap_l"] + s["ap_t"], 1),
                3),
            "close": (f"{s['close_w']}-{s['close_l']}"
                      + (f"-{s['close_t']}" if s["close_t"] else "")
                      if (s["close_w"] + s["close_l"] + s["close_t"]) else "—"),
            "close_n": s["close_w"] + s["close_l"] + s["close_t"],
        })

    # strongest first by the neutral-schedule measure, so the differential row
    # reads as deviation from where a team belongs
    rows.sort(key=lambda r: -r["ev"])

    print(f"\n{'tm':<6}{'rec':>6}{'pf':>8}{'xW':>7}{'diff':>7}"
          f"{'med':>7}{'all-play':>10}{'close':>7}")
    for r in rows:
        print(f"{r['abbrev']:<6}{r['record']:>6}{r['pf']:>8.1f}"
              f"{r['ev']:>7.2f}{r['diff']:>+7.2f}{r['median']:>7}"
              f"{r['everyone']:>10}{r['close']:>7}")

    OUT = processed_dir(league_key) / OUT_NAME
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "league": league_key, "label": LEAGUES[league_key]["label"],
        "season": season, "weeks": weeks, "n_teams": n,
        "close_margin": CLOSE_MARGIN, "rows": rows,
    }, indent=2))
    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    cli(main)
