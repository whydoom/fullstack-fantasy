"""Load ESPN's weekly player projections into the warehouse as a raw table.

    python -m src.load.espn_projections

ESPN's player pools, landed weekly by espn_team_weeks.py under
data/raw/espn/{league}/{season}/player_pool/weekNN.json, carry each player's
pre-game projection for that week as a stat line (statSourceId 1 = projected,
statSplitTypeId 1 = one week). This flattens those stat lines -- no renaming, no
scoring -- into raw_espn.player_projections:

    source_league, season, week, espn_id, stat_id, value

one row per projected stat, so dbt (stg_espn__player_projections) can name the
stats and score them under every league's rules, Yahoo leagues included: ESPN
projects NFL players, and the player ID crosswalk carries them to any platform.

Projections are ESPN's, not the league's: the same in every ESPN league, so the
pools are merged player by player -- each player-week from the first league whose
pool has it (one league's pool may carry kickers and defences the other's lacks). Same idempotence as the
other raw loaders: CREATE OR REPLACE from whatever is on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
RAW_ESPN = REPO_ROOT / "data" / "raw" / "espn"
SCHEMA, TABLE = "raw_espn", "player_projections"
PROJECTED, ONE_WEEK = 1, 1


def rows():
    seen = set()           # (season, week, espn_id): first league's pool wins
    for pool in sorted(RAW_ESPN.glob("*/*/player_pool/week*.json")):
        league, season = pool.parts[-4], int(pool.parts[-3])
        week = int(pool.stem.replace("week", ""))
        data = json.loads(pool.read_text(encoding="utf-8"))
        data = data[0] if isinstance(data, list) and data else data
        for entry in data.get("players") or []:
            p = entry.get("player") or {}
            if (season, week, p.get("id")) in seen:
                continue
            for line in p.get("stats") or []:
                if (line.get("statSourceId") == PROJECTED and line.get("statSplitTypeId") == ONE_WEEK
                        and line.get("scoringPeriodId") == week):
                    seen.add((season, week, p.get("id")))
                    for stat_id, value in (line.get("stats") or {}).items():
                        yield league, season, week, p.get("id"), int(stat_id), float(value)


def main():
    data = list(rows())
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    con.execute(f"CREATE OR REPLACE TABLE {SCHEMA}.{TABLE} (source_league VARCHAR, season INTEGER, "
                f"week INTEGER, espn_id BIGINT, stat_id INTEGER, value DOUBLE)")
    con.executemany(f"INSERT INTO {SCHEMA}.{TABLE} VALUES (?, ?, ?, ?, ?, ?)", data)
    n = con.execute(f"SELECT count(*), count(DISTINCT (season, week, espn_id)) FROM {SCHEMA}.{TABLE}").fetchone()
    print(f"{SCHEMA}.{TABLE}: {n[0]:,} projected stats for {n[1]:,} player-weeks")


if __name__ == "__main__":
    main()
