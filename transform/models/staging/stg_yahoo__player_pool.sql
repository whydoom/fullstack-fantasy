-- One row per league + period + position group + player: the top of Yahoo's player
-- pool by fantasy points, under this league's scoring, for one week or for the
-- season to date. Flatten, type, rename.
--
-- The period and position are not in the payload. They are parameters of the
-- request (`players;position=QB;...;sort_week=3/stats;type=week;week=3`), so they
-- are read back from the `_meta.resource_path` fetch_and_land recorded. That is the
-- one place a staging model parses the request rather than the response, and it is
-- why provenance is kept on every landed file.
--
-- Pages are only the top N per position (src/ingest/yahoo_season.PLAYER_POOL_DEPTH),
-- so this is a leaderboard source, not a complete player universe. Players are read
-- with the same depth-limited paths as stg_yahoo__roster_slots, for the same reason
-- (linked_player).

with source as (

    select * from {{ source('raw_yahoo', 'player_pool') }}

),

players as (

    select
        payload,
        payload ->> '$._meta.resource_path'                    as resource_path,
        unnest(json_extract(payload, '$.data..player'))        as player
    from source

),

renamed as (

    select
        -- Grain ---------------------------------------------------------------
        cast(payload ->> '$._meta.season' as integer)                         as season,
        regexp_extract(resource_path, '^league/([^/]+)/', 1)                   as league_key,
        -- 'W01'..'Wnn' for a week, 'SEASON' for season to date. A text key, not
        -- week = 0 or null: 0 would be a magic number, and a null in a key column
        -- makes uniqueness tests unreliable.
        case
            when resource_path like '%stats;type=season%' then 'SEASON'
            else 'W' || lpad(regexp_extract(resource_path, 'stats;type=week;week=(\d+)', 1), 2, '0')
        end                                                                   as period_key,
        -- Yahoo's position filter for the page: QB, RB, WR, TE, K, DEF or D.
        regexp_extract(resource_path, 'players;position=([^;]+)', 1)           as position_group,
        json_extract_string(player, '$[*][*].player_key')[1]                  as player_key,

        -- Context ---------------------------------------------------------------
        cast(nullif(regexp_extract(resource_path, 'stats;type=week;week=(\d+)', 1), '')
             as integer)                                                      as week,
        cast(json_extract_string(player, '$[*][*].player_id')[1] as integer)  as yahoo_player_id,
        json_extract_string(player, '$[*][*].name.full')[1]                   as player_name,
        json_extract_string(player, '$[*][*].display_position')[1]            as display_position,
        json_extract_string(player, '$[*][*].editorial_team_abbr')[1]         as nfl_team_abbr,

        -- Measure -----------------------------------------------------------------
        cast(json_extract_string(player, '$[*].player_points.total')[1]
             as decimal(7, 2))                                                as fantasy_points,

        cast(payload ->> '$._meta.fetched_at' as timestamptz)                 as fetched_at

    from players

)

select * from renamed
