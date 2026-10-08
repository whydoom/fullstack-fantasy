"""Dagster definitions for the fantasy analytics pipeline.

The dbt project (transform/) is loaded as Dagster assets via dagster-dbt's
`@dbt_assets` — one Dagster asset per dbt model/source/seed, wired into the same
lineage graph. That's the reason this project uses Dagster instead of Airflow: with
Airflow, "ingest raw data" and "run dbt" would be two tasks with no visibility into
which specific dbt *model* depends on which ingestion step.

Asset boundaries for ingestion (the open question, now answered)
---------------------------------------------------------------
**One asset per dataset, partitioned by season.** A failed or stale year
re-materializes on its own instead of forcing a full refetch, and the partition key
maps 1:1 onto the file actually written — partition "2023" of
`nflverse/player_stats_weekly` *is*
`data/raw/nflverse/player_stats_weekly/2023.parquet`. When a partition goes red in
the UI, the file it refers to is unambiguous.

The alternatives are worse for this data. One asset per source (all seasons, all
datasets) makes a single 404 on the newest season fail the entire backfill. One asset
per season (all datasets inside it) couples schedules to player stats, which are
published as separate upstream files on separate timelines — early in a season the
schedule exists and the stats do not, and that is a normal state, not a failure.

Two caveats worth stating rather than discovering later:

  1. The partition set is `StaticPartitionsDefinition`, built at code-load time from
     `seasons_to_fetch()`. A new season appears in the UI when the code location
     reloads, not spontaneously. That is fine at an annual cadence — and preferable to
     a yearly `TimeWindowPartitionsDefinition`, whose keys would be dates like
     "2023-01-01" rather than the "2023" that matches the filename.
  2. Season rollover is nflverse's definition, not the calendar's
     (`nfl.get_current_season()`), so the partition set and the files that exist
     upstream stay in sync. See src/ingest/nflverse.py.

The landing assets are season-partitioned; the DuckDB load assets deliberately are
not. The load is a `CREATE OR REPLACE` over the full glob of landed files — cheap
(~114k rows, well under a second) and always internally consistent. Partitioning it
would buy nothing and would let the table hold a mix of load generations.

Why the load assets are keyed `raw_nflverse/<dataset>`
------------------------------------------------------
That is exactly the asset key dagster-dbt derives for the dbt sources declared in
transform/models/staging/_nflverse__sources.yml. Matching it on purpose is what fuses
the two halves into one graph: landing -> DuckDB load -> dbt source -> staging model
appears as a single connected lineage rather than two disjoint islands that happen to
run in the same tool. Renaming either side without the other silently disconnects the
graph — dbt would show an orphaned source root and nothing would error.

The player ID crosswalk (nflreadpy's load_ff_playerids) breaks the partitioning
pattern above on purpose: it is a single current snapshot with no season dimension,
not one immutable fact per year, so `crosswalk_landing_asset` /
`crosswalk_load_asset` are single unpartitioned assets rather than season-keyed ones.
Same key-matching rule applies to the load asset (`raw_nflverse/player_id_crosswalk`)
so it fuses into the same dbt lineage graph as everything else.

The Yahoo ingestion (src/ingest/yahoo_client.py) is still not wired in: its asset
boundaries depend on payload shapes we have not seen yet, because API access is
pending review. Untouched here by design.
"""

import os
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    Definitions,
    MaterializeResult,
    MetadataValue,
    StaticPartitionsDefinition,
    asset,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from src.ingest.nflverse import (
    DATASETS,
    fetch_and_land,
    fetch_and_land_player_id_crosswalk,
    seasons_to_fetch,
)
from src.load import duckdb_raw

REPO_ROOT = Path(__file__).parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "transform"

# dbt-duckdb resolves a relative `path` in profiles.yml against the *process* working
# directory. The CLI is documented to run from the repo root, but dagster-dbt runs dbt
# from the project dir, which would silently point Dagster at a second, empty
# warehouse under transform/. Pinning the absolute path here makes every entry point
# — CLI, CI, Dagster — read and write the same file.
os.environ.setdefault("DBT_DUCKDB_PATH", str(REPO_ROOT / "data" / "warehouse.duckdb"))

# Partition keys are season strings, matching the landed filenames exactly.
season_partitions = StaticPartitionsDefinition(
    [str(season) for season in seasons_to_fetch()]
)


def _build_landing_asset(dataset: str):
    """One season-partitioned asset per nflverse dataset, landing raw parquet."""

    @asset(
        name=dataset,
        key_prefix="nflverse",
        partitions_def=season_partitions,
        group_name="nflverse_ingest",
        compute_kind="python",
        description=(
            f"Land nflverse {dataset} for one season, untouched, to "
            f"data/raw/nflverse/{dataset}/{{season}}.parquet."
        ),
    )
    def _landing_asset(context: AssetExecutionContext) -> MaterializeResult:
        season = int(context.partition_key)
        path = fetch_and_land(dataset, season)

        if path is None:
            # Expected for the current season before nflverse publishes it — the
            # season rolls over on the Thursday after Labor Day, but the stats file
            # appears only once games have been played. Materialize with a flag
            # rather than raising: nothing is broken, the data does not exist yet.
            context.log.info(
                f"nflverse has not published {dataset} for {season} yet; nothing landed."
            )
            return MaterializeResult(
                metadata={"landed": False, "reason": "not published upstream yet"}
            )

        return MaterializeResult(
            metadata={
                "landed": True,
                "path": MetadataValue.path(str(path)),
                "size_bytes": path.stat().st_size,
            }
        )

    return _landing_asset


def _build_load_asset(dataset: str):
    """Load every landed season of one dataset into the DuckDB raw schema.

    Keyed to match the dbt source asset key on purpose — see module docstring.
    """

    @asset(
        name=dataset,
        key_prefix=duckdb_raw.RAW_SCHEMA,
        deps=[["nflverse", dataset]],
        group_name="nflverse_ingest",
        compute_kind="duckdb",
        description=(
            f"CREATE OR REPLACE {duckdb_raw.RAW_SCHEMA}.{dataset} from every landed "
            "season. This is the dbt source the staging model reads."
        ),
    )
    def _load_asset(context: AssetExecutionContext) -> MaterializeResult:
        rows = duckdb_raw.load_one(dataset)
        context.log.info(f"{duckdb_raw.RAW_SCHEMA}.{dataset}: {rows:,} rows")
        return MaterializeResult(metadata={"rows": rows})

    return _load_asset


nflverse_landing_assets = [_build_landing_asset(name) for name in DATASETS]
nflverse_load_assets = [_build_load_asset(name) for name in DATASETS]


@asset(
    key_prefix="nflverse",
    name=duckdb_raw.CROSSWALK_TABLE,
    group_name="nflverse_ingest",
    compute_kind="python",
    description=(
        "Land today's snapshot of the player ID crosswalk (nflreadpy's "
        "load_ff_playerids -> DynastyProcess.com), including yahoo_id, to "
        "data/raw/nflverse/player_id_crosswalk/{date}.parquet."
    ),
)
def crosswalk_landing_asset(context: AssetExecutionContext) -> MaterializeResult:
    # Not partitioned like the season datasets above: this is a single current
    # snapshot with no season dimension, not one immutable fact per year — see
    # src/ingest/nflverse.py. Re-materializing later the same day is a no-op.
    path = fetch_and_land_player_id_crosswalk()
    return MaterializeResult(
        metadata={"path": MetadataValue.path(str(path)), "size_bytes": path.stat().st_size}
    )


@asset(
    key_prefix=duckdb_raw.RAW_SCHEMA,
    name=duckdb_raw.CROSSWALK_TABLE,
    deps=[["nflverse", duckdb_raw.CROSSWALK_TABLE]],
    group_name="nflverse_ingest",
    compute_kind="duckdb",
    description=(
        f"Load the single latest crosswalk snapshot into "
        f"{duckdb_raw.RAW_SCHEMA}.{duckdb_raw.CROSSWALK_TABLE}. Keyed to match the "
        "dbt source asset key, same reasoning as the season-dataset load assets."
    ),
)
def crosswalk_load_asset(context: AssetExecutionContext) -> MaterializeResult:
    rows = duckdb_raw.load_crosswalk_one()
    context.log.info(f"{duckdb_raw.RAW_SCHEMA}.{duckdb_raw.CROSSWALK_TABLE}: {rows:,} rows")
    return MaterializeResult(metadata={"rows": rows})

dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROJECT_DIR,
)
# Runs `dbt deps` + writes the manifest on `dagster dev`, so a fresh checkout doesn't
# need a manual `dbt deps` before the asset graph will load. No-ops outside dev
# (e.g. in CI or a deployed webserver), where the manifest is expected to already
# exist from a build step.
dbt_project.prepare_if_dev()


@dbt_assets(manifest=dbt_project.manifest_path)
def fantasy_dbt_assets(context, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


defs = Definitions(
    assets=[
        *nflverse_landing_assets,
        *nflverse_load_assets,
        crosswalk_landing_asset,
        crosswalk_load_asset,
        fantasy_dbt_assets,
    ],
    resources={
        "dbt": DbtCliResource(project_dir=dbt_project),
    },
)
