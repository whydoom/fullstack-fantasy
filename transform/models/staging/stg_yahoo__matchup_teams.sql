-- One row per league + week + team: each team's side of its head-to-head matchup.
-- A matchup has two sides, so each matchup appears twice, once from each team's
-- perspective — the shape every per-team question (record, points for/against)
-- wants, without a self-join in every consumer.
--
-- Same extraction approach as stg_yahoo__roster_slots: recursive descent to find
-- matchups and their teams, depth-limited paths for fields inside a team, and
-- tests/assert_yahoo_matchup_fields_single_valued.sql to prove each path matches
-- exactly once. Matchup-level fields (week, status, winner) sit directly on the
-- matchup object, so they are read with plain `$.key`.
--
-- Team metadata in this payload (faab_balance, managers, number_of_moves) is as of
-- the fetch, not as of the week — a week-1 scoreboard fetched in week 5 shows
-- week-5 balances. It is left out here on purpose; team attributes come from the
-- teams payload into dim_team_season.

with source as (

    select * from {{ source('raw_yahoo', 'scoreboards') }}

),

matchups as (

    select
        payload,
        unnest(json_extract(payload, '$.data..matchup')) as matchup
    from source

),

sides as (

    select
        payload,
        matchup,
        unnest(json_extract(matchup, '$..team')) as team
    from matchups

),

renamed as (

    select
        -- Grain ---------------------------------------------------------------
        cast(payload ->> '$._meta.season' as integer)                         as season,
        regexp_extract(
            json_extract_string(team, '$[*][*].team_key')[1],
            '^(.*)\.t\.\d+$', 1)                                              as league_key,
        cast(matchup ->> '$.week' as integer)                                 as week,
        json_extract_string(team, '$[*][*].team_key')[1]                      as team_key,

        -- The other side of the same matchup. Each matchup holds exactly two
        -- teams (asserted in the single-valued test), so the opponent is
        -- whichever of the pair is not this team.
        list_filter(
            json_extract_string(matchup, '$..team[*][*].team_key'),
            k -> k <> json_extract_string(team, '$[*][*].team_key')[1]
        )[1]                                                                  as opponent_team_key,

        -- Matchup context -------------------------------------------------------
        cast(matchup ->> '$.week_start' as date)                              as week_start_date,
        cast(matchup ->> '$.week_end' as date)                                as week_end_date,
        -- 'preevent' / 'midevent' / 'postevent'. Results are only final at
        -- postevent; the mart uses this to leave in-progress weeks undecided.
        matchup ->> '$.status'                                                as matchup_status,
        cast(matchup ->> '$.is_playoffs' as integer) = 1                      as is_playoffs,
        cast(matchup ->> '$.is_consolation' as integer) = 1                   as is_consolation,
        cast(matchup ->> '$.is_tied' as integer) = 1                          as is_tied,
        matchup ->> '$.winner_team_key'                                       as winner_team_key,

        -- This side's score -----------------------------------------------------
        cast(json_extract_string(team, '$[*].team_points.total')[1]
             as decimal(7, 2))                                                as team_points,
        cast(json_extract_string(team, '$[*].team_projected_points.total')[1]
             as decimal(7, 2))                                                as projected_points,

        -- Provenance --------------------------------------------------------------
        cast(payload ->> '$._meta.fetched_at' as timestamptz)                 as fetched_at

    from sides

)

select * from renamed
