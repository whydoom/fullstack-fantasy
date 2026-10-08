"""Last attempt at historical weekly lineups: the matchup/boxscore views.

mRoster + leagueHistory ignores scoringPeriodId (proven -- it returns the same
end-of-season snapshot for every week). But ESPN also carries lineups inside the
schedule objects, as rosterForMatchupPeriod / rosterForCurrentScoringPeriod on
each side of a matchup. Those are per-matchup by construction, so they may
survive in history where mRoster does not.

Same standard of proof as before: compare actual player IDs across weeks, not
entry counts.
"""

from __future__ import annotations

from src.ingest.espn import EspnClient, current_season

BENCH_SLOTS = {20, 21}
ROSTER_KEYS = ("rosterForCurrentScoringPeriod", "rosterForMatchupPeriod")


def fetch(client, season, week):
    params = [("view", "mBoxscore"), ("view", "mMatchupScore"),
              ("scoringPeriodId", week)]
    if season == current_season():
        return client.get(
            f"seasons/{season}/segments/0/leagues/{client.league_id}", params=params
        )
    return client.get(
        f"leagueHistory/{client.league_id}",
        params=[("seasonId", season)] + params,
    )


def lineup_for_team(obj, team_id, week):
    """Pull the lineup for one team from the schedule, for this matchup period."""
    for game in obj.get("schedule", []) or []:
        if game.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            s = game.get(side) or {}
            if s.get("teamId") != team_id:
                continue
            for key in ROSTER_KEYS:
                roster = s.get(key)
                if roster and roster.get("entries"):
                    return key, {
                        e["playerId"]: e.get("lineupSlotId")
                        for e in roster["entries"]
                    }
            return None, None
    return None, None


def check(season, weeks, team_id=1):
    client = EspnClient()
    print(f"\n=== {season}, team {team_id}, boxscore views ===")
    seen = {}
    for wk in weeks:
        try:
            obj = fetch(client, season, wk)
            obj = obj[0] if isinstance(obj, list) and obj else obj
        except Exception as exc:
            print(f"  week {wk:>2}: {type(exc).__name__} -- {str(exc)[:120]}")
            continue

        key, lineup = lineup_for_team(obj, team_id, wk)
        if not lineup:
            n_games = len(obj.get("schedule", []) or [])
            print(f"  week {wk:>2}: no lineup found ({n_games} games in schedule)")
            continue
        starters = {p for p, s in lineup.items() if s not in BENCH_SLOTS}
        seen[wk] = lineup
        print(f"  week {wk:>2}: {len(lineup)} players, {len(starters)} starters, "
              f"via {key}, hash {hash(frozenset(lineup.items())) % 100000:05d}")

    if len(seen) < 2:
        print("  not enough weeks to compare")
        return

    ws = sorted(seen)
    base = seen[ws[0]]
    identical = True
    print()
    for wk in ws[1:]:
        if seen[wk] == base:
            print(f"  week {wk:>2} vs {ws[0]}: IDENTICAL")
        else:
            identical = False
            diff = set(seen[wk]) ^ set(base)
            slot_changes = {p for p in set(seen[wk]) & set(base)
                            if seen[wk][p] != base[p]}
            print(f"  week {wk:>2} vs {ws[0]}: differs -- {len(diff)} roster changes, "
                  f"{len(slot_changes)} slot changes")

    print()
    print("  VERDICT: historical weekly lineups are NOT recoverable."
          if identical else
          "  VERDICT: historical weekly lineups ARE recoverable via boxscore views.")


if __name__ == "__main__":
    check(2024, [1, 5, 9, 14])
    check(2021, [1, 5, 9, 14])   # the season mRoster returned nothing for
    check(2018, [1, 5, 9, 13])   # oldest season, to find the history floor
