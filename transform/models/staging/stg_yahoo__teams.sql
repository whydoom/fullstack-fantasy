-- One row per league + team: the team as Yahoo describes it at fetch time, with its
-- manager. Flatten, type, rename.
--
-- Left out on purpose: faab_balance, waiver_priority, number_of_moves,
-- number_of_trades, roster_adds. Those are running counters that change every week,
-- not attributes of the team; they belong to a FAAB/transactions model with a time
-- dimension, not to a team dimension where they would silently go stale.
--
-- Managers: Yahoo hides every manager's account guid (returned as "--hidden--"), so
-- nothing here identifies a person across seasons. manager_id is just 1..n within
-- this league-season, and nickname is user-editable. That is why dim_manager is not
-- built from this model; cross-season identity needs a maintained mapping.

with source as (

    select * from {{ source('raw_yahoo', 'teams') }}

),

teams as (

    select
        payload,
        unnest(json_extract(payload, '$.data..team')) as team
    from source

),

renamed as (

    select
        -- Grain -----------------------------------------------------------------
        cast(payload ->> '$._meta.season' as integer)                         as season,
        regexp_extract(
            json_extract_string(team, '$[*][*].team_key')[1],
            '^(.*)\.t\.\d+$', 1)                                              as league_key,
        json_extract_string(team, '$[*][*].team_key')[1]                      as team_key,
        cast(json_extract_string(team, '$[*][*].team_id')[1] as integer)      as team_id,

        -- Team ------------------------------------------------------------------
        json_extract_string(team, '$[*][*].name')[1]                          as team_name,
        json_extract_string(team, '$[*][*].url')[1]                           as team_url,
        json_extract_string(team, '$[*][*].team_logos[*].team_logo.url')[1]   as team_logo_url,
        -- Finish in this league the prior season; null for a team new this year.
        cast(nullif(json_extract_string(team, '$[*][*].previous_season_team_rank')[1], '')
             as integer)                                                      as previous_season_rank,

        -- Manager -----------------------------------------------------------------
        -- One manager per team, asserted by the single-valued test. A co-managed
        -- team would fail it rather than have one manager picked arbitrarily.
        cast(json_extract_string(team, '$[*][*].managers[*].manager.manager_id')[1]
             as integer)                                                      as manager_id,
        json_extract_string(team, '$[*][*].managers[*].manager.nickname')[1]  as manager_nickname,
        -- Yahoo only sends is_commissioner when it is true.
        coalesce(
            json_extract_string(team, '$[*][*].managers[*].manager.is_commissioner')[1] = '1',
            false)                                                            as is_commissioner,

        -- Provenance ----------------------------------------------------------------
        cast(payload ->> '$._meta.fetched_at' as timestamptz)                 as fetched_at

    from teams

)

select * from renamed
