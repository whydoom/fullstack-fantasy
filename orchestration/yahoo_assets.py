"""Dagster assets for the Yahoo leagues: landing -> raw load -> (dbt) -> site.

The graph for one league, ADN today, reads left to right in the UI:

    yahoo/adn  ->  raw_yahoo/*  ->  stg_yahoo__* / fct_* / dim_*  ->  site_data/adn  ->  site/adn
    (land JSON)   (DuckDB load)     (dbt, from definitions.py)        (export JSON)      (render pages)

Asset boundaries, and why
-------------------------
* Landing is one asset per LEAGUE, not per resource or per week. yahoo_season.py
  already decides, call by call, what is final (lands once) and what is still
  changing (re-fetched), so a weekly partition here would duplicate that rule in a
  second place that could drift from it. The unit a person cares about is "is
  ADN's data current", which is exactly one asset.
* The load is one multi-asset producing every raw_yahoo/<table>. src/load/yahoo_raw
  rebuilds all tables in one pass (CREATE OR REPLACE over every landed file), and
  the keys match the asset keys dagster-dbt derives for the sources in
  _yahoo__sources.yml -- the same fusing trick the nflverse loads use, so landing
  and dbt appear as one connected lineage rather than two islands.
* dbt is not redefined here. definitions.py's @dbt_assets already includes every
  Yahoo model; Dagster connects them through those raw_yahoo keys, and the dbt
  tests (grain, reconciliation, n/2) run as asset checks on each build.
* Export and render are separate assets because they fail for different reasons:
  export can only fail on the warehouse, render only on the site checkout.

The weekly schedule
-------------------
Tuesday 8:00 US Eastern (America/New_York, so it tracks the clock through daylight
saving rather than staying fixed at UTC-5). Monday night's game has finished, so the
week is final;
the next week has not kicked off, so Yahoo's season-to-date totals equal the sum of
the final weeks the page shows (see yahoo_site_export.py). Stat corrections keep
arriving for a few days, which is why the landing re-fetches the previous week too.

The schedule is defined as RUNNING, so it is on as soon as the code loads; Dagster's
default is stopped until someone switches it on in the UI, and `dagster dev` without
a DAGSTER_HOME does not remember that switch between sessions. It still only fires
while the Dagster daemon is running, which locally means while `dagster dev` is up. Nothing here runs unattended on a server; that would need a
host, which is a cost decision, not a code one.
"""

# No `from __future__ import annotations`: Dagster inspects the `context`
# annotation at definition time and rejects it as a postponed string.
import os
import subprocess
import sys
from pathlib import Path

import duckdb
from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    DefaultScheduleStatus,
    Failure,
    MaterializeResult,
    MetadataValue,
    ScheduleDefinition,
    asset,
    define_asset_job,
    multi_asset,
)

from src.ingest.discover import latest_league_key
from src.ingest.leagues import LEAGUES, keys
from src.ingest.yahoo_season import land_season, season_for
from src.load import yahoo_raw

REPO_ROOT = Path(__file__).resolve().parent.parent

# The site repo the render step writes into. Defaults to the sibling checkout;
# JIMOODY_SITE_DIR overrides it (e.g. in .env) without a path living in this public
# repo's code.
SITE_DIR = Path(os.environ.get("JIMOODY_SITE_DIR", REPO_ROOT.parent / "jimoody.com"))
RENDERERS = ["render_leaders_board.py", "render_team_weeks.py",
             "render_luck.py", "render_faab.py"]

# The marts and staging models the export reads. Declared as deps so the export
# waits for dbt, and so the lineage graph shows exactly what the site is built from.
EXPORT_INPUTS = [
    "dim_team_season", "fct_roster_slot", "fct_matchup", "fct_player_rank",
    "fct_team_week_allplay", "fct_transaction", "stg_yahoo__roster_slots",
    "stg_yahoo__roster_positions", "stg_yahoo__matchup_teams",
    "stg_yahoo__team_snapshots", "stg_yahoo__transaction_players",
]


def _league_key(league: str) -> str:
    return latest_league_key(LEAGUES[league]["yahoo_league_name"])


def _run_script(context, args: list, cwd: Path) -> None:
    """Run a repo script as its own process, the way it runs from the command line.

    The export and render steps are scripts, not library code: they live at a repo
    root (or in another repo entirely), so they are invoked, not imported. Dagster
    starts each step in a fresh process where those roots are not on sys.path, and
    invoking keeps the command line the single tested entry point.
    """
    proc = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8")
    if proc.stdout.strip():
        context.log.info(f"{args[0]}:\n{proc.stdout.strip()}")
    if proc.returncode != 0:
        raise Failure(f"{args[0]} failed:\n{proc.stderr.strip()}")


def _build_landing_asset(league: str):
    @asset(
        name=league,
        key_prefix="yahoo",
        group_name="yahoo_ingest",
        compute_kind="python",
        description=(
            f"Land {LEAGUES[league]['label']}'s current Yahoo season (settings, teams, "
            "transactions, weekly scoreboards, rosters, player pool) to "
            "data/raw/{season}/{league_key}/. Final weeks land once; the live and "
            "previous week re-fetch. See src/ingest/yahoo_season.py."
        ),
    )
    def _landing(context: AssetExecutionContext) -> MaterializeResult:
        league_key = _league_key(league)
        season = season_for(league_key)
        land_season(league_key)
        root = yahoo_raw.RAW_DIR / season / league_key
        files = sum(1 for _ in root.rglob("*.json"))
        context.log.info(f"{league_key}: {files} raw files under {root}")
        return MaterializeResult(metadata={
            "league_key": league_key, "season": season, "raw_files": files,
            "path": MetadataValue.path(str(root)),
        })

    return _landing


yahoo_landing_assets = [_build_landing_asset(league) for league in keys("yahoo")]


@multi_asset(
    specs=[
        AssetSpec(
            key=[yahoo_raw.RAW_SCHEMA, table],
            deps=[a.key for a in yahoo_landing_assets],
            group_name="yahoo_ingest",
            description=(f"CREATE OR REPLACE {yahoo_raw.RAW_SCHEMA}.{table} from every "
                         "landed file. The dbt source its staging model reads."),
        )
        for table in yahoo_raw.RESOURCES
    ],
    compute_kind="duckdb",
)
def yahoo_raw_load(context: AssetExecutionContext):
    for table, files in yahoo_raw.load_all().items():
        context.log.info(f"{yahoo_raw.RAW_SCHEMA}.{table}: {files} files")
        yield MaterializeResult(asset_key=[yahoo_raw.RAW_SCHEMA, table],
                                metadata={"files": files})


def _build_export_asset(league: str):
    @asset(
        name=league,
        key_prefix="site_data",
        deps=[AssetKey(m) for m in EXPORT_INPUTS],
        group_name="yahoo_site",
        compute_kind="python",
        description=(f"Export {LEAGUES[league]['label']}'s marts as the site's JSON: "
                     "leaders, team weeks, luck (yahoo_site_export.py) and FAAB "
                     f"(faab_{league}.py), under data/processed/{league}/."),
    )
    def _export(context: AssetExecutionContext) -> MaterializeResult:
        league_key = _league_key(league)
        _run_script(context, ["yahoo_site_export.py", "--league", league], REPO_ROOT)
        if (REPO_ROOT / f"faab_{league}.py").exists():
            _run_script(context, [f"faab_{league}.py"], REPO_ROOT)
        else:
            context.log.info(f"No faab_{league}.py; {league} has no FAAB section.")

        # Surface the one known inconsistency as a warning on the run, not just a
        # line in stdout: exported mid-week, season totals include the live week.
        con = duckdb.connect(str(yahoo_raw.WAREHOUSE_PATH), read_only=True)
        try:
            live = con.execute(
                "select min(week) from stg_yahoo__matchup_teams "
                "where league_key = ? and matchup_status = 'midevent'", [league_key]
            ).fetchone()[0]
        finally:
            con.close()
        if live is not None:
            context.log.warning(
                f"Week {live} is in progress: season-to-date totals include it, so "
                "Season cells will not equal the sum of the weeks shown.")
        out = REPO_ROOT / "data" / "processed" / league
        return MaterializeResult(metadata={
            "path": MetadataValue.path(str(out)),
            "files": sorted(p.name for p in out.glob("*.json")),
            "live_week": live if live is not None else "none",
        })

    return _export


def _build_page_asset(league: str):
    @asset(
        name=league,
        key_prefix="site",
        deps=[AssetKey(["site_data", league])],
        group_name="yahoo_site",
        compute_kind="python",
        description=(f"Render public/{LEAGUES[league]['page']} in the jimoody.com "
                     "checkout (JIMOODY_SITE_DIR). Writes files only: committing and "
                     "deploying stay manual."),
    )
    def _page(context: AssetExecutionContext) -> MaterializeResult:
        if not (SITE_DIR / "leagues_site.py").exists():
            raise Failure(
                f"No jimoody.com checkout at {SITE_DIR}. Set JIMOODY_SITE_DIR in .env "
                "to the site repo's path.")
        for script in RENDERERS:
            _run_script(context, [script, "--league", league], SITE_DIR)
        page = SITE_DIR / "public" / LEAGUES[league]["page"]
        return MaterializeResult(metadata={"page": MetadataValue.path(str(page))})

    return _page


yahoo_export_assets = [_build_export_asset(league) for league in keys("yahoo")]
yahoo_page_assets = [_build_page_asset(league) for league in keys("yahoo")]

# Everything downstream of ADN's landing: the raw load, the Yahoo dbt models and
# their tests, the export and the page. Nothing nflverse or ESPN is selected.
adn_weekly_refresh = define_asset_job(
    name="adn_weekly_refresh",
    selection=AssetSelection.assets(AssetKey(["yahoo", "adn"])).downstream(),
    description="Land, load, model, export and render the ADN league.",
)

adn_weekly_schedule = ScheduleDefinition(
    job=adn_weekly_refresh,
    cron_schedule="0 8 * * 2",
    execution_timezone="America/New_York",
    default_status=DefaultScheduleStatus.RUNNING,
    description="Tuesday 8:00 ET: week final, next week not yet kicked off.",
)
