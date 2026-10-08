"""Find out what the ESPN league actually contains before modeling anything.

Run this first. It answers four questions that drive every downstream decision:
  1. Do the cookies work at all?
  2. How many seasons of history exist?
  3. What are the scoring rules and roster slots (IDP? half-PPR?)
  4. What does the roster payload look like -- is started-vs-benched in there?
"""

from __future__ import annotations

import json

from src.ingest.espn import RAW_DIR, EspnClient, EspnAuthError, current_season


def summarize(payload, year):
    """leagueHistory returns a list; the live season path returns an object."""
    obj = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(obj, dict):
        print(f"  unexpected payload type: {type(obj)}")
        return

    settings = obj.get("settings", {}) or {}
    teams = obj.get("teams", []) or []
    scoring = settings.get("scoringSettings", {}) or {}
    roster = settings.get("rosterSettings", {}) or {}

    print(f"  league name      : {settings.get('name')}")
    print(f"  teams            : {len(teams)}  (settings says {settings.get('size')})")
    print(f"  current week     : {obj.get('scoringPeriodId')}")
    print(f"  regular season   : {settings.get('scheduleSettings', {}).get('matchupPeriodCount')} weeks")
    print(f"  scoring items    : {len(scoring.get('scoringItems', []) or [])}")
    print(f"  playerRatios set : {bool(scoring.get('playerRatios'))}")

    slots = roster.get("lineupSlotCounts", {}) or {}
    active = {k: v for k, v in slots.items() if v}
    print(f"  lineup slots     : {active}")

    # Roster shape -- the thing fct_roster_slot depends on
    sample_team = next((t for t in teams if t.get("roster")), None)
    if sample_team:
        entries = sample_team["roster"].get("entries", []) or []
        print(f"  roster entries   : {len(entries)} on team {sample_team.get('id')}")
        if entries:
            e = entries[0]
            print(f"  entry keys       : {sorted(e.keys())}")
            print(f"  lineupSlotId     : {e.get('lineupSlotId')} "
                  f"(slot id tells you started vs bench)")
    else:
        print("  roster entries   : none in payload -- mRoster view may need "
              "scoringPeriodId, or history does not retain rosters")


def main():
    client = EspnClient()
    cur = current_season()
    print(f"league {client.league_id}, current season {cur}\n")

    # 1. cheapest possible auth check
    try:
        payload = client.season(cur, views=["mTeam"])
    except EspnAuthError as e:
        print("AUTH FAILED\n")
        print(e)
        return
    print("auth OK\n")

    # 2. walk back until a season returns nothing
    found = []
    for year in range(cur, cur - 12, -1):
        try:
            p = client.fetch_and_land(year)
        except Exception as exc:
            print(f"{year}: {type(exc).__name__} -- {str(exc)[:160]}")
            continue
        obj = p[0] if isinstance(p, list) and p else p
        if not obj or not isinstance(obj, dict) or not obj.get("teams"):
            print(f"{year}: no league data")
            continue
        found.append(year)
        print(f"\n=== {year} ===")
        summarize(p, year)

    print(f"\nseasons with data: {found}")
    print(f"raw payloads in {RAW_DIR}")


if __name__ == "__main__":
    main()
