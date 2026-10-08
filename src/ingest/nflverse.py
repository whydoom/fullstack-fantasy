"""Ingestion for nflverse public NFL data, via the `nflreadpy` package.

Second data source alongside the Yahoo Fantasy API, and a permanent one — the two
answer different questions. Yahoo knows what *this league* did: who drafted whom, who
started whom, who won. nflverse knows what the *players* did: real NFL box scores,
schedules, opponents. Joining them is what makes questions like "was that start/sit
call defensible given the matchup?" answerable at all. Neither source can answer that
alone.

No authentication. nflverse publishes as static parquet assets on GitHub releases, so
there is no API key, no OAuth handshake, and no rate limit to respect — which is also
why this module is far shorter than yahoo_client.py.

Package choice: `nflreadpy`, NOT `nfl_data_py`
---------------------------------------------
`nfl_data_py` is the package most tutorials still reference, and it is **deprecated**.
Upstream's own README: "nfl_data_py has been deprecated in favour of nflreadpy. All
future development will occur in nflreadpy and users are encouraged to switch
immediately. No further nfl_data_py maintenance or updates are planned."

It is also not merely discouraged but unusable here: `nfl_data_py` pins an old pandas
that has no cp312 wheel, so pip falls back to building it from source and the build
fails on modern setuptools (`ModuleNotFoundError: No module named 'pkg_resources'`).
Verified against this repo's pinned 3.12 venv. See CLAUDE.md.

`nflreadpy` returns **polars** DataFrames, not pandas. Nothing here converts them:
polars writes parquet natively, and DuckDB reads that parquet natively, so pandas is
never in the path. Do not add a `.to_pandas()` for familiarity's sake — it would cost
a full copy and re-type every column for no benefit.

Landing format: parquet, not JSON
---------------------------------
The Yahoo client lands raw JSON because Yahoo *returns* JSON, and re-serializing it
any other way would destroy the payload this project's ELT pattern depends on. This
source returns dataframes that were parquet upstream to begin with. Round-tripping
them through JSON would throw away the column types and force staging models to
re-guess them. So: same ELT principle — land the source's native form, untouched,
shape it in dbt — but a different physical format, because the sources' native forms
differ. This asymmetry is recorded in CLAUDE.md as a deliberate decision.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import nflreadpy as nfl
import polars as pl

RAW_DATA_DIR = Path("data/raw/nflverse")

# nflverse's weekly player stats start well before this, but 2020 is the horizon the
# project cares about and there is no reason to land seasons no mart will read.
FIRST_SEASON = 2020

# Dataset name -> loader. The name is also the directory under data/raw/nflverse/,
# so adding a dataset is a one-line change here plus a dbt source + staging model.
#
# Play-by-play (nfl.load_pbp) is deliberately absent: tens of thousands of rows and
# 300+ columns per season, and nothing in the current model needs snap-level detail.
# Add it here when something does.
DATASETS: dict[str, Callable[[int], pl.DataFrame]] = {
    # summary_level="week" is the default, but state it: the same function returns
    # season totals under a different argument, and a silent default flip upstream
    # would change the grain of this table without changing this file.
    "player_stats_weekly": lambda season: nfl.load_player_stats(
        seasons=[season], summary_level="week"
    ),
    "schedules": lambda season: nfl.load_schedules(seasons=[season]),
}


def current_season() -> int:
    """The season nflverse itself considers current.

    Deliberately not derived from the calendar. nflverse rolls this over on its own
    schedule, and that rollover is exactly what decides which files exist upstream:
    on 2026-09-09 this returns 2025, and asking for 2026 player stats 404s because
    nflverse has not published them yet. Deriving "current season" from the system
    clock would put those two definitions out of sync and produce spurious failures
    every September.
    """
    return nfl.get_current_season()


def seasons_to_fetch() -> list[int]:
    return list(range(FIRST_SEASON, current_season() + 1))


def _raw_path(dataset: str, season: int) -> Path:
    return RAW_DATA_DIR / dataset / f"{season}.parquet"


def fetch_and_land(dataset: str, season: int) -> Path | None:
    """Fetch one dataset-season and land it to data/raw/nflverse/{dataset}/{season}.parquet.

    Returns the path written, the existing path if the fetch was skipped, or None if
    the data is not published upstream yet.

    Idempotent, with one deliberate exception. Completed seasons are immutable, so an
    existing file for a *past* season is never re-fetched — same rule as the Yahoo
    client, and what makes a backfill cheap to re-run after a crash. The **current
    season is always re-fetched**, because it is the one season that is still
    changing: scores land weekly and nflverse issues stat corrections against games
    already played. Skipping it on "file exists" would freeze the pipeline at whatever
    week happened to be current the first time this ran — an idempotency rule quietly
    becoming a staleness bug.
    """
    if dataset not in DATASETS:
        raise KeyError(f"Unknown dataset {dataset!r}. Known: {sorted(DATASETS)}")

    dest = _raw_path(dataset, season)
    is_current = season >= current_season()

    if dest.exists() and not is_current:
        return dest

    try:
        frame = DATASETS[dataset](season)
    except ConnectionError as exc:
        # nflverse publishes each season as a release asset; one that does not exist
        # yet 404s. That is an expected state for the current season early in the
        # year, not a failure — the season simply has not been published. Any other
        # connection error is a real problem and must not be swallowed, or a network
        # outage would look like a successful run that landed nothing.
        if "404" in str(exc):
            return None
        raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(
        dest,
        # Provenance as parquet key-value metadata rather than extra columns or a
        # sidecar file. Same intent as the Yahoo client's `_meta` wrapper — record
        # what was fetched and when — without touching a single cell of the data.
        metadata={
            "source": "nflverse",
            "dataset": dataset,
            "season": str(season),
            "nflreadpy_version": nfl.__version__,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return dest


def backfill(datasets: list[str] | None = None) -> None:
    """Land every dataset for every season from FIRST_SEASON through the current one."""
    for dataset in datasets or list(DATASETS):
        for season in seasons_to_fetch():
            existed = _raw_path(dataset, season).exists()
            path = fetch_and_land(dataset, season)
            if path is None:
                status = "not published upstream yet"
            elif existed and season < current_season():
                status = "skipped (already landed, season complete)"
            else:
                schema = pl.read_parquet_schema(path)
                status = f"landed {len(schema)} cols -> {path}"
            print(f"{dataset:<20} {season}  {status}")


# --------------------------------------------------------------- Player ID crosswalk
#
# Not in DATASETS above, and deliberately landed differently, because it isn't the
# same kind of thing. player_stats_weekly and schedules are one immutable fact per
# season — 2023's games don't change after 2023 ends. This crosswalk
# (nflreadpy.load_ff_playerids, sourced from DynastyProcess.com) is the opposite: a
# single **current-snapshot** table of player identity across platforms — no season
# dimension at all, and it *should* drift as players change teams and new players get
# IDs assigned. Landed now, ahead of any actual Yahoo join, because it is the
# permanent join key this project needs to link nflverse's player_id to Yahoo's
# player identity by ID rather than by fuzzy name matching once Yahoo access lands.
#
# Confirmed columns include `gsis_id` (matches this project's player_id format,
# e.g. "00-0031636") and `yahoo_id`. Coverage against players actually landed here:
# 3,449 of 4,061 (84.9%) resolve a gsis_id match — the gap is expected, since this
# crosswalk is fantasy-relevance-driven and skips many non-skill-position players.

CROSSWALK_DIR = RAW_DATA_DIR / "player_id_crosswalk"


def _crosswalk_path(as_of: date) -> Path:
    return CROSSWALK_DIR / f"{as_of.isoformat()}.parquet"


def latest_crosswalk_path() -> Path | None:
    """Most recently landed crosswalk snapshot, or None if none has ever landed."""
    files = sorted(CROSSWALK_DIR.glob("*.parquet"))
    return files[-1] if files else None


def fetch_and_land_player_id_crosswalk(as_of: date | None = None) -> Path:
    """Land one day's crosswalk snapshot to data/raw/nflverse/player_id_crosswalk/{date}.parquet.

    Idempotent per day, not per season: re-running later the same day is a no-op: the
    file for today already exists. Running on a new day lands a new dated file rather
    than overwriting yesterday's — past snapshots stay immutable, same principle as
    the season files, just dated instead of seasoned. This preserves the audit trail
    (which players' ID mappings existed as of which date) that this project's ELT
    design treats as a hard requirement, even though the loader (src/load/duckdb_raw)
    only ever reads the single latest snapshot back into the warehouse.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    dest = _crosswalk_path(as_of)
    if dest.exists():
        return dest

    frame = nfl.load_ff_playerids()
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(
        dest,
        metadata={
            "source": "nflverse (nflreadpy.load_ff_playerids -> DynastyProcess.com)",
            "dataset": "player_id_crosswalk",
            "snapshot_date": as_of.isoformat(),
            "nflreadpy_version": nfl.__version__,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return dest


def main() -> None:
    args = sys.argv[1:]
    unknown = [a for a in args if a not in DATASETS]
    if unknown:
        print(
            f"Unknown dataset(s): {', '.join(unknown)}\n"
            f"Usage: python -m src.ingest.nflverse [{' | '.join(DATASETS)}]\n"
            "  With no arguments, backfills every season-partitioned dataset and\n"
            "  lands today's player ID crosswalk snapshot."
        )
        raise SystemExit(2)

    backfill(args or None)

    today_path = _crosswalk_path(datetime.now(timezone.utc).date())
    already_landed_today = today_path.exists()
    path = fetch_and_land_player_id_crosswalk()
    status = "already landed for today" if already_landed_today else f"landed -> {path}"
    print(f"{'player_id_crosswalk':<20} {'-':<6} {status}")


if __name__ == "__main__":
    main()
