-- One row per player, current cross-platform ID mapping. Flatten, type, rename.
--
-- Scope: the upstream table (DynastyProcess.com, via nflreadpy) carries ~20 platform
-- ID columns (mfl_id, sportradar_id, fantasypros_id, pff_id, ...) plus draft and bio
-- trivia (height, weight, college, twitter_username). This model keeps the IDs this
-- project can actually use — gsis_id (joins to player_id elsewhere in this project),
-- yahoo_id (the reason this table was landed at all), espn_id and sleeper_id (cheap
-- to keep, no cost to carrying two more string columns) — and drops the rest. Same
-- "curated wide" reasoning as stg_nflverse__player_stats_weekly: promoting a dropped
-- column later is a one-line addition, not a re-fetch.
--
-- gsis_id is not unique in the raw snapshot (10 of 8,007 non-null values repeat
-- upstream). Deduplicated by qualify below rather than filtered out, because the one
-- duplicate that actually overlaps this project's landed player data (00-0031636,
-- listed twice for two position tags) is a real player this project needs to keep a
-- mapping for, not noise to discard.

with source as (

    select * from {{ source('raw_nflverse', 'player_id_crosswalk') }}

),

renamed as (

    select
        gsis_id                                 as player_id,
        yahoo_id,
        espn_id,
        sleeper_id,
        name                                    as full_name,
        position                                as position_code,
        team                                    as team_abbr

    from source

    -- A row with no gsis_id can never join to this project's nflverse-keyed models —
    -- it exists in the crosswalk for platforms this project doesn't otherwise touch.
    where gsis_id is not null

    qualify row_number() over (
        partition by gsis_id
        -- Deterministic, not meaningful: ties are upstream data-quality noise (see
        -- source yml), so any consistent choice is as good as any other. Preferring
        -- the row with a yahoo_id maximizes usefulness for the join this table exists
        -- to support.
        order by (yahoo_id is null), name
    ) = 1

)

select * from renamed
