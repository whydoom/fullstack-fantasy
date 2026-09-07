"""Dagster definitions for the Yahoo fantasy analytics pipeline.

The dbt project (transform/) is loaded as Dagster assets via dagster-dbt's
`@dbt_assets` — one Dagster asset per dbt model/source/seed, wired into the same
lineage graph. That's the reason this project uses Dagster instead of Airflow: with
Airflow, "ingest raw JSON" and "run dbt" would be two tasks with no visibility into
which specific dbt *model* depends on which ingestion step. Here, once ingestion is
wired in as its own assets (TODO — see below), the whole pipeline from Yahoo API to
marts is one graph.

Ingestion (src/ingest) is not yet wired in as Dagster assets: we haven't decided the
per-resource asset boundaries (one asset per season? per resource type?) because we
haven't seen real payload shapes yet. `transform/` is scaffolded and loads cleanly
today, so it's wired in first.
"""

from pathlib import Path

from dagster import Definitions
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

REPO_ROOT = Path(__file__).parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "transform"

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
def yahoo_fantasy_dbt_assets(context, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


defs = Definitions(
    assets=[yahoo_fantasy_dbt_assets],
    resources={
        "dbt": DbtCliResource(project_dir=dbt_project),
    },
)
