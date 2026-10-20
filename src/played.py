"""Did a player play in his NFL game that week? nflverse decides, the platforms only
when it cannot.

The platforms' own signal is a stat line: Yahoo's is empty for a player who played
but recorded nothing (an IDP with no tackles, a tight end with no targets), so it
reads as "did not play"; ESPN's can hold a zero for a player who never took the
field. nflverse's snap counts and player stats settle it: a player with a snap or a
stat line that week played.

Only where nflverse has already counted that team's game (snap counts land up to a
day after it) -- before then, and for a player with no gsis id, the platform's flag
stands. Read from the warehouse; no API calls.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
NAMES = ROOT / "transform" / "seeds" / "player_names.csv"


@lru_cache(maxsize=None)
def _nfl(season: int):
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        snaps = {(g, int(w)) for g, w in con.execute("""
            select x.gsis_id, s.week
            from main.stg_nflverse__snap_counts s
            join (select pfr_id, min(gsis_id) as gsis_id from raw_nflverse.player_id_crosswalk
                  where pfr_id is not null and gsis_id is not null group by 1) x
              on x.pfr_id = s.pfr_player_id
            where s.season = ? and coalesce(s.offense_snaps, 0) + coalesce(s.defense_snaps, 0)
                  + coalesce(s.special_teams_snaps, 0) > 0""", [season]).fetchall()}
        stats = {(g, int(w)) for g, w in con.execute("""
            select player_id, week from main.stg_nflverse__player_stats_weekly where season = ?""",
            [season]).fetchall()}
        counted = {(t, int(w)) for t, w in con.execute("""
            select distinct team_abbr, week from main.stg_nflverse__snap_counts where season = ?""",
            [season]).fetchall()}
        team = {(g, int(w)): t for g, w, t in con.execute("""
            select player_id, week, team_abbr from main.stg_nflverse__rosters_weekly where season = ?""",
            [season]).fetchall()}
    except duckdb.CatalogException:
        snaps, stats, counted, team = set(), set(), set(), {}
    finally:
        con.close()
    return snaps | stats, counted, team


@lru_cache(maxsize=None)
def _gsis(platform: str):
    with NAMES.open(encoding="utf-8", newline="") as fh:
        return {r[f"{platform}_id"]: r["gsis_id"] for r in csv.DictReader(fh)
                if r[f"{platform}_id"] and r["gsis_id"]}


def played(platform: str, player_id, season: int, week: int, fallback: bool) -> bool:
    """True if nflverse shows him on the field (a snap or a stat line) that week,
    False if it counted his team's game and he is not in it, else `fallback`."""
    g = _gsis(platform).get(str(player_id).split(".p.")[-1])
    if not g:
        return fallback
    on_field, counted, team = _nfl(int(season))
    w = int(week)
    if (g, w) in on_field:
        return True
    t = team.get((g, w))
    if t and (t, w) in counted:
        return False
    return fallback
