"""Prove that scoringPeriodId actually changes the roster returned.

Identical entry counts across weeks are not evidence of anything -- ESPN may be
ignoring the parameter and returning the same snapshot. The only real test is
whether the player IDs and their lineup slots differ week to week.

If every week comes back identical, historical lineup decisions are NOT
recoverable and fct_roster_slot can only be built going forward.
"""

from __future__ import annotations

from src.ingest.espn import EspnClient, current_season

BENCH_SLOTS = {20, 21}  # 20 = bench, 21 = IR


def roster_fingerprint(client, season, week, team_id=1):
    if season == current_season():
        data = client.get(
            f"seasons/{season}/segments/0/leagues/{client.league_id}",
            params=[("view", "mRoster"), ("scoringPeriodId", week)],
        )
    else:
        data = client.get(
            f"leagueHistory/{client.league_id}",
            params=[("seasonId", season), ("view", "mRoster"),
                    ("scoringPeriodId", week)],
        )
    obj = data[0] if isinstance(data, list) and data else data
    team = next((t for t in obj.get("teams", []) if t.get("id") == team_id), None)
    if not team or not team.get("roster"):
        return None, None
    entries = team["roster"].get("entries", []) or []
    # map playerId -> lineupSlotId
    roster = {e["playerId"]: e.get("lineupSlotId") for e in entries}
    starters = {p for p, s in roster.items() if s not in BENCH_SLOTS}
    return roster, starters


def check(season, weeks):
    client = EspnClient()
    print(f"\n=== {season}, team 1 ===")
    seen = {}
    for wk in weeks:
        roster, starters = roster_fingerprint(client, season, wk)
        if roster is None:
            print(f"  week {wk:>2}: no roster returned")
            continue
        seen[wk] = (roster, starters)
        print(f"  week {wk:>2}: {len(roster)} players, {len(starters)} starters, "
              f"roster hash {hash(frozenset(roster.items())) % 100000:05d}")

    if len(seen) < 2:
        print("  not enough weeks to compare")
        return

    weeks_sorted = sorted(seen)
    base_wk = weeks_sorted[0]
    base_roster, base_starters = seen[base_wk]
    all_identical = True

    print()
    for wk in weeks_sorted[1:]:
        roster, starters = seen[wk]
        if roster == base_roster:
            print(f"  week {wk:>2} vs {base_wk}: IDENTICAL")
        else:
            all_identical = False
            added = set(roster) - set(base_roster)
            dropped = set(base_roster) - set(roster)
            slot_changes = {p for p in set(roster) & set(base_roster)
                            if roster[p] != base_roster[p]}
            print(f"  week {wk:>2} vs {base_wk}: differs -- "
                  f"{len(added)} added, {len(dropped)} dropped, "
                  f"{len(slot_changes)} slot changes")
            start_diff = starters ^ base_starters
            if start_diff:
                print(f"        {len(start_diff)} players moved in/out of the lineup")

    print()
    if all_identical:
        print("  VERDICT: scoringPeriodId appears to be IGNORED.")
        print("  Historical weekly lineups are not recoverable this way.")
    else:
        print("  VERDICT: scoringPeriodId works. Weekly lineup history is real.")


if __name__ == "__main__":
    # Spread the weeks out -- consecutive weeks on a quiet roster can legitimately
    # match, which would be a false negative.
    check(2024, [1, 5, 9, 14])
    check(2026, [1, 3, 5])
