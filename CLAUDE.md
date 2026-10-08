# Yahoo Fantasy Football Portfolio Analytics

Personal analytics project on the user's Yahoo fantasy football league, built to
demonstrate analytics engineering rigor (BI Analyst -> Analytics Engineer transition
portfolio piece). Every decision below should be defensible in an interview.

## Design decisions (do not re-litigate; ask the user if you disagree)

- **Warehouse: DuckDB.** Free, no billing account, no bill-risk. dbt keeps the SQL
  portable to BigQuery/Snowflake later if the project needs to scale or demonstrate
  cloud-warehouse skills.
- **Transformation: dbt-core + dbt-duckdb.**
- **Orchestration: Dagster OSS**, not Airflow. Dagster's asset model maps directly onto
  dbt models, giving one lineage graph across ingestion and transformation instead of
  two disconnected systems (DAG of tasks vs. DAG of models).
- **CI: GitHub Actions running `dbt build` on PRs.** Public repo.
- **Ingestion: raw Python + `requests`.** Deliberately NOT yfpy/yahoofantasy or any SDK.
  SDKs parse the API response into objects before you ever see it, which destroys the
  raw payload the ELT pattern below depends on.
- **ELT, not ETL.** Land raw data untouched to `data/raw/`, do all shaping/typing in dbt.
  The raw layer is the audit trail and lets you replay transformations without re-hitting
  the source.
- **Raw landing format follows the source, and therefore differs by source.** Yahoo
  lands JSON; nflverse lands parquet. This asymmetry is deliberate — see
  "Landing format is per-source" below. It is not drift to be tidied up.
- **Two data sources, permanently.** The Yahoo Fantasy API and nflverse
  (`src/ingest/nflverse.py`). Neither is a stopgap for the other; they answer
  different questions and the interesting marts join them.
- **Everything must cost $0.** Ask the user before introducing anything with a cost —
  including paid tiers of otherwise-free services (e.g. Dagster Cloud, hosted warehouses).

## Hard constraints

- **Public repo. Never commit credentials or any Yahoo data.** `.env`, `.secrets/`,
  `data/raw/`, and `*.duckdb` are gitignored — verify new file types that could carry
  secrets or league data get added to `.gitignore` before they're ever written.
- **Read-only API access.** This project must never write to a Yahoo league (no roster
  moves, no trades, no settings changes). All ingestion calls are GET requests against
  read endpoints.

## Environment: Python version is pinned to 3.12, not the system default

The machine's default/only installed Python was 3.14 (via `py -0p`). That combination
does not work:

- `dbt-core` + `dbt-duckdb` install fine on 3.14.
- `dagster` 1.13.x supports 3.14, but **`dagster-dbt`'s latest release (0.28.8) pins
  `dagster==1.12.8` exactly**, and `dagster` 1.12.x's `Requires-Python` upper bound is
  `<3.14`. So on Python 3.14 no `dagster` + `dagster-dbt` combination resolves.
- Worse: if you `pip install dagster dagster-dbt` on 3.14 *without* pinning, pip does
  **not** error — it silently resolves `dagster-dbt` down to `0.10.9` (an ancient
  pre-1.0 release with a loose/unbounds `dagster` requirement) just to satisfy the
  solver. `0.10.9`'s API (no `@dbt_assets`, no `DbtCliResource`) is wildly incompatible
  with `dagster` 1.12.8's API. `pip check` reports no conflict because version *ranges*
  are satisfied — but the packages don't actually work together. This is the failure
  mode to watch for: **a clean `pip check` is not proof the Dagster+dbt integration
  works.** Always verify by pinning explicitly and checking `pip show dagster-dbt`
  reports the version you intended, not whatever the resolver found convenient.

Fix applied: installed Python 3.12 via `winget install --id Python.Python.3.12`
alongside the system 3.14 (no system Python changes; `py -3.12` selects it), and built
the project venv against it. Pinned versions (`requirements.txt` is the source of truth):

```
dagster==1.12.8
dagster-webserver==1.12.8
dagster-dbt==0.28.8
dbt-core==1.10.23      # pulled down by dagster-dbt's own dbt-core pin
dbt-duckdb==1.11.0
```

If you ever `pip install -U` anything in this stack, re-verify the lockstep: check
what exact `dagster` version the target `dagster-dbt` release requires
(`pip download <pkg>==<version> --no-deps` and inspect metadata, or trial-install)
before letting the resolver pick versions on its own.

Side effect of the pin: `dagster-dbt==0.28.8` itself pins `dbt-core==1.10.23`, which
is below dbt's current latest (1.12.x) and prints a "this version is deprecated"
warning on every invocation. That warning is expected and not a sign of a broken
setup — it's the cost of the Dagster-native lineage integration. It's noisy but
harmless; don't chase it away with `--upgrade` without redoing the lockstep check
above.

**Always activate `.venv` (built with `py -3.12 -m venv .venv`) before running anything
in this repo.** Do not use system `python`/`pip` directly — that resolves to 3.14.

## Structure

```
src/ingest/          Extraction (yahoo_client.py, discover.py, nflverse.py)
src/load/            Landed files -> DuckDB raw schemas (duckdb_raw.py)
transform/           dbt project (raw -> staging -> marts)
orchestration/       Dagster definitions
data/raw/            Landed source data, gitignored:
                       data/raw/{season}/{name}.json               (Yahoo)
                       data/raw/nflverse/{dataset}/{season}.parquet (nflverse)
.github/workflows/   CI (ingest nflverse + dbt build on PR)
```

## dbt layering

- **raw**: source data as landed.
  - *nflverse* — settled. `src/load/duckdb_raw.py` loads the parquet into schema
    `raw_nflverse` as real tables; dbt sources point at those tables, not at files.
  - *Yahoo* — still TBD, pending real payloads. `_yahoo__sources.yml` currently
    guesses at a `read_json_auto` glob. When Yahoo lands for real, prefer the
    nflverse approach (load in Python, source the table) for the cwd reason above.
- **staging**: one model per resource — flatten, type, rename. No business logic.
  Naming: `stg_{source}__{resource}`, e.g. `stg_nflverse__player_stats_weekly`.
  Written and passing for nflverse (including the player ID crosswalk); not yet
  written for Yahoo.
- **marts**: dimensional model. Two atomic facts, one per source, until Yahoo lands
  and the two can be joined: `fct_player_week` (nflverse, real NFL production) and
  the not-yet-built `fct_roster_slot` (Yahoo, what was rostered).

Built and passing (nflverse side):
- `dim_player` — `player_id` (nflverse's GSIS id). Most-recently-known attributes,
  type-1 snapshot; carries a nullable `yahoo_id` for the future Yahoo join.
- `dim_game` — `game_id`. Thin pass-through of the staging model; nothing to add.
- `fct_player_week` — `player_id + season + week + season_type`. Also a thin
  pass-through: the fact grain and staging grain are identical, so the mart layer's
  real contribution is the tested FK contract to `dim_player`/`dim_game`, not any
  transformation.

Still planned (Yahoo side, grain in parens) — schema/tests scaffolded in
`_marts__schema.yml`, SQL not written pending real payloads:
- `dim_manager` — one row per human, tracked across seasons and team-name changes
- `dim_team_season` — `league_key + team_id`
- `fct_roster_slot` — `league_key + week + team + player` (started vs benched, points).
  This is the atomic fact on the Yahoo side, the counterpart to `fct_player_week`.
- `fct_matchup` — `league_key + week + team`
- `fct_draft_pick` — `league_key + pick_number`
- `fct_transaction` — `transaction_id + player`

Do not guess at Yahoo column shapes; land data first.

### Wide vs. split-by-family: why `stg_nflverse__player_stats_weekly` is wide

nflverse ships 150 columns for weekly player stats. The choice was between one wide
staging model and several narrower ones split by stat family (passing / rushing /
receiving / kicking / defense). **Wide, curated to ~45 columns, won** — and it isn't
close, because splitting by family doesn't change the grain here the way splitting
normally would.

Splitting into separate tables earns its complexity when sub-entities have their own
grain — e.g. a "passing_attempts" table with one row per pass thrown would be a real
grain, distinct from "one row per player-game." That's not this data: nflverse has
already pre-aggregated everything to the player-game grain before it ever reaches
this project. A player has exactly one passing-stats row, one rushing-stats row, and
one receiving-stats row for a given game — they're all the same row. Splitting by
family would not create three grains; it would shard one row's columns across three
tables that must be rejoined by every consumer that touches more than one family
(which is most of them — a dual-threat QB or a receiving RB needs two "families" for
a single fantasy-points calculation). And DuckDB/parquet are columnar already, so
splitting buys no scan-cost benefit a single wide table doesn't already have.

The real decision was narrower: not "wide vs. split" but "wide vs. curated-wide."
Passing through all 150 columns was rejected too — most of them are deep kicking/
punting/defensive splits (`fg_made_40_49`, `pt_inside_20`, `def_pat_blocks`, ...)
nothing in this project currently reads. The staging model keeps ~45: identity, game
context, and offensive/kicking production plus fantasy points. Everything else stays
queryable in `raw_nflverse.player_stats_weekly` and is a one-line addition to promote
— the raw layer is not lossy, the staging model is deliberately scoped.

## Yahoo API notes (see src/ingest/discover.py docstring for the gory detail)

- Yahoo's JSON is hostile: collections are objects keyed by `"0"`, `"1"`, `"2"`, ... plus
  a sibling `"count"` key, and array elements mix metadata dicts with sub-resource dicts
  in the same list. **Never index-chase a fixed path.** Recursively walk the tree and
  collect dicts by the key you're looking for (e.g. any dict containing `league_key`).
  This is deliberate: Yahoo's schema drifts across seasons and endpoints, and a raw
  layer's entire value proposition is surviving that drift.
- Game keys (the numeric prefix in a `league_key`) change every season and must be
  discovered via the API, never hardcoded.
- OAuth2 three-legged flow, manual code paste (no listener on the redirect URI) because
  this is a personal-use script, not a registered web app with a live callback endpoint.
- Append `?format=json` to every request; Yahoo defaults to XML.
- Throttle to ~1 req/sec — no documented rate limit, but this is a personal script
  against a personal league; there's no reason to hammer it.
- Completed seasons are immutable, so `fetch_and_land` skips re-fetching a resource if
  its file already exists. This makes historical backfills idempotent and cheap to
  re-run after a crash.

## nflverse: the second data source (`src/ingest/nflverse.py`)

Public NFL data — weekly player box scores and schedules, 2020 through the current
season. No API key, no OAuth, no rate limit: nflverse publishes static parquet as
GitHub release assets. Added while Yahoo API access was pending review, but permanent
regardless: Yahoo knows what *this league* did (who drafted, started, won), nflverse
knows what the *players* did (real box scores, opponents, schedule). Questions like
"was that start/sit defensible given the matchup?" need both.

### Use `nflreadpy`, NOT `nfl_data_py`

`nfl_data_py` is the package most tutorials and most model training data still point
at. It is **deprecated by its own authors**: "nfl_data_py has been deprecated in
favour of nflreadpy. All future development will occur in nflreadpy... No further
nfl_data_py maintenance or updates are planned."

It is also simply not installable here: it pins a pandas with no cp312 wheel, and the
source build fails on modern setuptools (`ModuleNotFoundError: No module named
'pkg_resources'`). Verified by trial install against this repo's venv. So the choice
was forced, not stylistic.

Function names differ between the two — `import_weekly_data()` / `import_schedules()`
became `load_player_stats()` / `load_schedules()`. If you are about to write
`nfl.import_*`, you are working from stale memory.

`nflreadpy` returns **polars**, not pandas. Nothing converts it: polars writes parquet,
DuckDB reads parquet, pandas is never in the path. Do not add `.to_pandas()`.

Installing it does **not** disturb the dagster/dbt lockstep — it only adds polars,
polars-runtime, pydantic-settings. Verified with `pip install --dry-run` first, per
the lockstep rule above, and re-verified with `pip show` after.

### Landing format is per-source: parquet here, JSON for Yahoo

Yahoo returns JSON, so Yahoo lands JSON — re-encoding it would destroy the payload the
raw layer exists to preserve. nflverse returns dataframes that were parquet upstream
to begin with, so nflverse lands parquet. Round-tripping those through JSON would
throw away every column type and force staging models to re-guess them.

Same ELT principle in both cases — land the source's native form untouched, shape it
in dbt. Different physical format, because the sources' native forms differ. Do not
"harmonize" these to one format; that would mean corrupting one source to match the
other.

Provenance still gets recorded, just in the format's own idiom: the Yahoo client wraps
payloads in a `_meta` key, and the nflverse writer stores the same fields as parquet
key-value metadata, so not a single cell of data is touched.

### Idempotency has an exception the Yahoo client doesn't need

Completed seasons are immutable, so an existing file for a past season is never
re-fetched — same rule as Yahoo. But the **current season is always re-fetched**: it
is the one season still changing, as scores land weekly and nflverse issues stat
corrections. Skipping it on "file exists" would freeze the pipeline at whatever week
was current the first time it ran — an idempotency rule quietly becoming a staleness
bug.

"Current season" comes from `nfl.get_current_season()`, **not** the calendar. nflverse
rolls the season over on the Thursday after Labor Day, and that rollover decides which
files exist upstream. On 2026-09-09 it returns 2025, and requesting 2026 player stats
404s because nflverse has not published them. A calendar-derived year would desync from
the files and throw spurious errors every September. A 404 on the current season is
therefore an expected state, handled and logged, not a failure — but any *other*
connection error is re-raised, so a network outage can never masquerade as a
successful run that landed nothing.

### Load step: `src/load/duckdb_raw.py`

Parquet is loaded into DuckDB as schema `raw_nflverse`, one table per dataset, via
`CREATE OR REPLACE ... SELECT * FROM read_parquet(..., union_by_name = true)`. No
shaping — that is dbt's job.

Loading into tables rather than pointing dbt sources at the parquet with
`external_location` is deliberate: `external_location` is resolved by DuckDB against
the *process* cwd, which differs between the CLI (repo root) and dagster-dbt (project
dir). A relative glob cannot be right for both, and **the failure is silent** — a glob
matching nothing reads as an empty source, not an error. Resolving paths once in
Python from an absolute repo root removes the whole class of bug.

The same cwd hazard applies to the warehouse file itself: `orchestration/definitions.py`
pins `DBT_DUCKDB_PATH` to an absolute path so the CLI, CI, and Dagster all read and
write `data/warehouse.duckdb` rather than Dagster quietly creating a second, empty
warehouse under `transform/`. (A stray empty `transform/data/warehouse.duckdb` from
before this fix is still on disk and is safe to delete.)

### Player ID crosswalk (`nflreadpy.load_ff_playerids`)

Landed ahead of any actual Yahoo integration, specifically so the future Yahoo join
can be done by ID instead of by fuzzy-matching player names. Source: DynastyProcess.com,
via nflreadpy — the successor package's equivalent of what the prompt asked
`nfl_data_py` for (`nfl_data_py` never exposed this table under this project's chosen
package; the function name is `load_ff_playerids`, not anything with "crosswalk" in it).

**This table breaks the season-partition pattern on purpose.** `player_stats_weekly`
and `schedules` are one immutable fact per season. This is the opposite: a single
**current-snapshot** table of player identity across ~20 platforms, no season
dimension, and it is *supposed* to drift as players change teams and new players get
IDs assigned. So it lands differently: one dated file per day it's fetched
(`data/raw/nflverse/player_id_crosswalk/{date}.parquet`, immutable once written, same
as everywhere else in this project — but dated, not seasoned), and the loader
(`load_player_id_crosswalk` in `src/load/duckdb_raw.py`) reads back only the single
*latest* dated file, not a union of every snapshot ever landed. Unioning them would
turn "today's ID mapping" into "one row per player per day this pipeline has ever
run," which is not what the table means.

Verified before landing, not assumed:
- Confirmed columns include `gsis_id` (matches this project's `player_id` format,
  e.g. `"00-0031636"`) and `yahoo_id`.
- Coverage: 3,449 of the 4,061 real players landed here (84.9%) resolve a `gsis_id`
  match. The gap is expected — this crosswalk is fantasy-relevance-driven and skips
  many non-skill-position players.
- `gsis_id` is not globally unique in the raw snapshot (10 of 8,007 non-null values
  repeat), but only one of those ten duplicates overlaps a player actually landed
  here, and it's a genuine duplicate of the same person (listed under two position
  tags), not a collision between two different real players. Deduplicated with a
  deterministic `qualify row_number()` in `stg_nflverse__player_id_crosswalk`.
- `yahoo_id` here is assumed to be the reusable numeric portion of Yahoo's
  `player_key` (`game_key.p.player_id`), per Yahoo's documented key structure. **Not
  yet verified against a real Yahoo payload** — confirm before relying on it for an
  actual join once Yahoo access lands.

## Decisions made while scaffolding (not explicitly specified — flagging for review)

- **Added `dbt-labs/dbt_utils` as a package dependency** (`transform/packages.yml`),
  used for `unique_combination_of_columns` generic tests on every composite-key mart
  (`dim_team_season`, `fct_roster_slot`, `fct_matchup`, `fct_draft_pick`,
  `fct_transaction`). dbt-core has no built-in composite-uniqueness test. It's free
  and the de facto standard package, but it is a dependency you didn't explicitly ask
  for — say so if you'd rather hand-roll these as a custom singular test instead.
- **`transform/models/staging/_yahoo__sources.yml` is a best guess**, not derived
  from real payloads: six sources (`leagues`, `teams`, `rosters`, `matchups`,
  `draft_results`, `transactions`) each reading `data/raw/{season}/{name}.json` via
  DuckDB's `read_json_auto` glob. The actual `name` values `src/ingest` lands files
  under aren't decided yet (e.g. rosters might land per-week, not per-season). Treat
  this file as a placeholder to reconcile once ingestion writes real files, not as a
  contract ingestion must match.
- **Marts schema.yml intentionally references models that don't exist yet.**
  `dbt debug` and `dbt build` both pass regardless (verified) — dbt treats a
  schema.yml entry for a missing model as a warning, not a build error, and orphaned
  tests are just skipped.
  - **This is no longer why CI is green.** CI now builds real models against real
    data: it runs `python -m src.ingest.nflverse` and `python -m src.load.duckdb_raw`
    before `dbt build`, which it can do because nflverse needs no credentials (~4 MB).
    So the grain and relationship tests run for real on every PR. A green CI run is
    now evidence of something. The Yahoo half still cannot run in CI — it needs OAuth
    credentials this public repo must never hold — so when Yahoo models land, gate
    them out of the CI selection rather than adding secrets.
- **Ingestion asset boundaries: one asset per dataset, partitioned by season.**
  Decided and implemented for nflverse. A failed or stale year re-materializes alone
  instead of forcing a full refetch, and the partition key maps 1:1 onto the file
  written — partition "2023" *is* `.../player_stats_weekly/2023.parquet`. One asset
  per source would let a single 404 on the newest season fail an entire backfill; one
  asset per season would couple schedules to player stats, which are published as
  separate upstream files on separate timelines. Yahoo should follow the same pattern
  once its payload shapes are known.
  - The partition set is `StaticPartitionsDefinition`, built at code-load time, so a
    new season appears on code-location reload rather than spontaneously — fine at an
    annual cadence, and it keeps partition keys as `"2023"` rather than the
    `"2023-01-01"` a yearly `TimeWindowPartitionsDefinition` would force.
  - The DuckDB load assets are deliberately **not** partitioned: the load is a
    `CREATE OR REPLACE` over the full glob, cheap and always internally consistent.
  - The load assets are keyed `raw_nflverse/<dataset>` **to match the asset key
    dagster-dbt derives for the dbt sources**. That match is what fuses ingestion and
    dbt into one lineage graph (landing -> load -> dbt source -> staging). Rename one
    side without the other and the graph silently splits in two — dbt shows an
    orphaned source root and nothing errors.

## Testing / verification expectations

- `dbt debug` must pass against `transform/` before considering ingestion "wired up."
- Run `python -m py_compile` (or equivalent) on new ingestion modules before considering
  them done — catching syntax errors here is cheap; catching them mid-OAuth-flow is not.
- **Never write a dbt test asserting a grain you haven't checked against landed data.**
  A test that passes because it was guessed correctly is indistinguishable from one
  that passes vacuously. The nflverse grains were confirmed with `group by ... having
  count(*) > 1` before the tests were written — which is how the 131 identity-less
  rows in player stats were found (see the `where` clause in
  `stg_nflverse__player_stats_weekly`).
- **A green `dbt build` locally is not proof CI is green.** Local runs have landed
  data; CI starts empty. Verify by building against a fresh warehouse
  (`DBT_DUCKDB_PATH=/tmp/x.duckdb dbt build ...`), or from a clean copy of only the
  files that would actually be committed.
- **Never hardcode a week count.** 2020's regular season ran through week 17; 2021
  onward runs through 18 (the NFL expanded its schedule), and postseason weeks
  continue the numbering at 21-22 rather than resetting to 1. Any model that needs
  "how many weeks this season" must derive it from landed data (e.g.
  `max(week)` from `dim_game` filtered to `game_type`), never assume a constant —
  see the `week` column doc on `fct_player_week`.
