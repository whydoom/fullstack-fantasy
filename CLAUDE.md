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
- **ELT, not ETL.** Land raw JSON untouched to `data/raw/`, do all shaping/typing in dbt.
  The raw layer is the audit trail and lets you replay transformations without re-hitting
  the API.
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
src/ingest/          Python extraction (yahoo_client.py, discover.py)
transform/           dbt project (raw -> staging -> marts)
orchestration/        Dagster definitions
data/raw/            landed JSON, gitignored, organized data/raw/{season}/{name}.json
.github/workflows/   CI (dbt build on PR)
```

## dbt layering

- **raw**: JSON as landed (dbt sources pointing at `data/raw/`, loaded via DuckDB's
  `read_json_auto` or an external table / seed-style load — landing mechanism TBD once
  we've seen real payloads).
- **staging**: one model per resource — flatten, type, rename. No business logic.
- **marts**: dimensional model. `fct_roster_slot` is the atomic grain; every other mart
  rolls up from it.

Planned marts (grain in parens):
- `dim_manager` — one row per human, tracked across seasons and team-name changes
- `dim_team_season` — `league_key + team_id`
- `dim_player` — `player_key`
- `fct_roster_slot` — `league_key + week + team + player` (started vs benched, points)
- `fct_matchup` — `league_key + week + team`
- `fct_draft_pick` — `league_key + pick_number`
- `fct_transaction` — `transaction_id + player`

Model SQL is intentionally not written yet — schema/sources/tests are scaffolded, but
we haven't seen real Yahoo payloads. Do not guess at column shapes; land data first.

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
  tests are just skipped. This is why the very first CI run on this scaffold is green
  with zero models: there's nothing to build yet, and that's expected, not a broken
  pipeline.
- **Dagster wires up `transform/` as `@dbt_assets` immediately; `src/ingest/` is not
  yet wired in as Dagster assets.** The per-resource asset boundaries for ingestion
  (one asset per season? per resource type?) depend on decisions we haven't made
  about how granularly to land files, which in turn depends on real payload shapes.
  `orchestration/definitions.py` currently only orchestrates the dbt layer.

## Testing / verification expectations

- `dbt debug` must pass against `transform/` before considering ingestion "wired up."
- Run `python -m py_compile` (or equivalent) on new ingestion modules before considering
  them done — catching syntax errors here is cheap; catching them mid-OAuth-flow is not.
