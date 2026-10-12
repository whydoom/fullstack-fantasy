"""Load landed Yahoo JSON into the DuckDB warehouse as schema raw_yahoo.

The Yahoo counterpart to duckdb_raw.py, and for the same reason: paths are resolved
here, in Python, from an absolute repo root, so dbt sources are plain tables and
never depend on whichever cwd invoked dbt (see that module's docstring).

One row per landed file, two columns: the file's path and its JSON, untouched.
Nothing is parsed, flattened or typed here. Yahoo's payloads are deeply nested,
collection-as-object JSON (see src/ingest/discover.py), and `read_json_auto` would
infer a struct schema from them — which is itself a parse, and a lossy one, since
sibling list elements of different shapes collapse to whatever the sampler guessed.
`read_json_objects` keeps each document as a single JSON value, so the staging
models see exactly what Yahoo sent (plus the `_meta` wrapper fetch_and_land adds).

Idempotent: CREATE OR REPLACE rebuilds each table from whatever is currently landed,
so a re-fetched in-progress week propagates without a manual drop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
RAW_DIR = REPO_ROOT / "data" / "raw"

RAW_SCHEMA = "raw_yahoo"

# table -> glob under data/raw, laid out as {season}/{league_key}/... by the landing
# names fetch_and_land was given. Add a resource here when its staging model is
# written, not before: a raw table nothing reads is just an unverified guess.
#
# The prefix pins Yahoo's layout — a season directory (starts with a digit) holding a
# league_key directory (contains ".l.") — because data/raw is shared with other
# sources. A bare */*/teams.json would also match a future espn/{league}/teams.json
# and silently load ESPN payloads into raw_yahoo.
_LEAGUE_DIR = "[0-9]*/*.l.*"
RESOURCES = {
    "rosters": f"{_LEAGUE_DIR}/rosters/week_*/*.json",
    "scoreboards": f"{_LEAGUE_DIR}/scoreboard_week_*.json",
    "teams": f"{_LEAGUE_DIR}/teams.json",
}


def load_resource(connection: duckdb.DuckDBPyConnection, table: str) -> int:
    """Rebuild raw_yahoo.<table> from every landed file. Returns the row count."""
    glob = (RAW_DIR / RESOURCES[table]).as_posix()
    connection.execute(
        f"CREATE OR REPLACE TABLE {RAW_SCHEMA}.{table} AS "
        "SELECT filename AS landed_file, json AS payload "
        "FROM read_json_objects($glob, filename = true)",
        {"glob": glob},
    )
    return connection.execute(
        f"SELECT count(*) FROM {RAW_SCHEMA}.{table}"
    ).fetchone()[0]


def load_all(tables: list[str] | None = None) -> dict[str, int]:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
        return {t: load_resource(connection, t) for t in (tables or list(RESOURCES))}
    finally:
        connection.close()


def main() -> None:
    args = sys.argv[1:]
    unknown = [a for a in args if a not in RESOURCES]
    if unknown:
        print(
            f"Unknown table(s): {', '.join(unknown)}\n"
            f"Usage: python -m src.load.yahoo_raw [{' | '.join(RESOURCES)}]"
        )
        raise SystemExit(2)
    for table, rows in load_all(args or None).items():
        print(f"{RAW_SCHEMA}.{table:<20} {rows:>7,} files")
    print(f"\nWarehouse: {WAREHOUSE_PATH}")


if __name__ == "__main__":
    main()
