"""Load landed nflverse parquet into the DuckDB warehouse as a raw schema.

This is the `data/raw/*  ->  raw.*` arrow in the README's four-layer diagram: the
step between landing files and dbt. It does no shaping — no renames, no casts, no
filters. `SELECT *` from the parquet into a table of the same columns. All typing and
renaming belongs in dbt staging models, per the ELT rationale in CLAUDE.md.

Why load into tables at all, instead of pointing dbt sources straight at the parquet
via dbt-duckdb's `external_location`?

  Path resolution. `external_location` is a bare string that DuckDB resolves against
  the *process* working directory, which differs by caller: the CLI is documented to
  run from the repo root, while Dagster's DbtCliResource runs dbt from the project
  dir. A relative glob cannot be correct for both, and the failure is silent — a glob
  matching nothing reads as an empty source, not an error. Resolving paths once here,
  in Python, from an absolute repo root, means dbt never has to know where the files
  are on disk.

Idempotent by construction: CREATE OR REPLACE rebuilds each table from whatever is
currently landed. That makes the load safe to re-run at any time, and means a
re-fetched current season propagates without a manual drop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

from src.ingest.nflverse import DATASETS, RAW_DATA_DIR, latest_crosswalk_path

# Absolute, derived from this file — never from the caller's cwd. See module docstring.
REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"

# dbt sources read from here. Kept separate from the (future) raw Yahoo schema so the
# two sources stay legible as distinct lineage roots rather than one mixed namespace.
RAW_SCHEMA = "raw_nflverse"


def load_dataset(connection: duckdb.DuckDBPyConnection, dataset: str) -> int:
    """Rebuild raw_nflverse.<dataset> from every landed season. Returns the row count."""
    glob = (REPO_ROOT / RAW_DATA_DIR / dataset / "*.parquet").as_posix()

    connection.execute(
        f"CREATE OR REPLACE TABLE {RAW_SCHEMA}.{dataset} AS "
        # union_by_name guards against schema drift across seasons: nflverse adds
        # columns over time, and a positional union would silently misalign them.
        # The seasons landed today happen to share one schema; a permanent source
        # should not assume next year's will match.
        "SELECT * FROM read_parquet($glob, union_by_name = true)",
        {"glob": glob},
    )
    return connection.execute(
        f"SELECT count(*) FROM {RAW_SCHEMA}.{dataset}"
    ).fetchone()[0]


CROSSWALK_TABLE = "player_id_crosswalk"


def load_player_id_crosswalk(connection: duckdb.DuckDBPyConnection) -> int:
    """Load the single latest crosswalk snapshot into raw_nflverse.player_id_crosswalk.

    Unlike load_dataset, this does NOT glob every landed file — the crosswalk lands
    one dated snapshot per day precisely so past snapshots stay on disk as an audit
    trail (see src/ingest/nflverse.py), but there is only ever one *current* mapping
    to model. Unioning every date landed would multiply each player's row by however
    many days this has been run, which is not what a "current ID mapping" table means.
    """
    path = latest_crosswalk_path()
    if path is None:
        raise FileNotFoundError(
            "No player_id_crosswalk snapshot landed yet. Run "
            "`python -m src.ingest.nflverse` first."
        )

    connection.execute(
        f"CREATE OR REPLACE TABLE {RAW_SCHEMA}.{CROSSWALK_TABLE} AS "
        "SELECT * FROM read_parquet($path)",
        {"path": path.as_posix()},
    )
    return connection.execute(
        f"SELECT count(*) FROM {RAW_SCHEMA}.{CROSSWALK_TABLE}"
    ).fetchone()[0]


def load_all(datasets: list[str] | None = None) -> dict[str, int]:
    """Rebuild every requested dataset. Returns {dataset: row_count}."""
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
        return {
            dataset: load_dataset(connection, dataset)
            for dataset in (datasets or list(DATASETS))
        }
    finally:
        connection.close()


def load_one(dataset: str) -> int:
    """Rebuild a single dataset and return its row count.

    The unit Dagster materializes: one asset per dataset, so the orchestrator never
    has to manage a warehouse connection itself.
    """
    return load_all([dataset])[dataset]


def load_crosswalk_one() -> int:
    """Rebuild raw_nflverse.player_id_crosswalk. The unit Dagster materializes."""
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
        return load_player_id_crosswalk(connection)
    finally:
        connection.close()


def main() -> None:
    args = sys.argv[1:]
    unknown = [a for a in args if a not in DATASETS]
    if unknown:
        print(
            f"Unknown dataset(s): {', '.join(unknown)}\n"
            f"Usage: python -m src.load.duckdb_raw [{' | '.join(DATASETS)}]\n"
            "  With no arguments, loads every season-partitioned dataset plus the\n"
            "  player ID crosswalk."
        )
        raise SystemExit(2)

    for dataset, rows in load_all(args or None).items():
        print(f"{RAW_SCHEMA}.{dataset:<20} {rows:>7,} rows")
    print(f"{RAW_SCHEMA}.{CROSSWALK_TABLE:<20} {load_crosswalk_one():>7,} rows")
    print(f"\nWarehouse: {WAREHOUSE_PATH}")


if __name__ == "__main__":
    main()
