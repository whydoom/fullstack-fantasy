-- One row per league + week + team + player: who was on a roster that week, which
-- lineup slot they were in, and what they scored. Flatten, type, rename. No business
-- logic — "started" vs "benched" is a mart decision, not a staging one.
--
-- Finding players uses JSONPath recursive descent (`$..player`), not a fixed path.
-- Yahoo serializes collections as objects keyed "0", "1", ... and mixes metadata
-- dicts with sub-resources inside the same list, and both shapes drift between
-- seasons. A fixed path like team[1].roster."0".players breaks on the first drift;
-- `$..player` finds every player wherever it sits — the SQL form of the tree walk
-- src/ingest/discover.py does in Python.
--
-- Fields *within* a player are depth-limited instead: `$[*][*].key` matches a key
-- exactly two levels down (any list position, but no deeper). Unbounded descent was
-- tried first and is wrong here: a player can carry a nested `linked_player` with
-- its own player_key/player_id (Travis Hunter, 470.p.99002, links to 470.p.99001),
-- and `$..player_key` returned both. Wildcards keep the drift tolerance on list
-- positions without reaching into sub-objects.
--
-- Every path returns a list, so each field takes element [1]. That is only safe if
-- each path matches exactly once; tests/assert_yahoo_roster_fields_single_valued.sql
-- checks that against the raw payload rather than assuming it — and is the test
-- that caught the linked_player case.

with source as (

    select * from {{ source('raw_yahoo', 'rosters') }}

),

players as (

    select
        payload,
        unnest(json_extract(payload, '$.data..player')) as player
    from source

),

renamed as (

    select
        -- Grain -------------------------------------------------------------
        cast(payload ->> '$._meta.season' as integer)                         as season,
        -- team_key is '{game}.l.{league}.t.{team}'; the league key is its prefix.
        regexp_extract(
            json_extract_string(payload, '$.data..team_key')[1],
            '^(.*)\.t\.\d+$', 1)                                              as league_key,
        cast(json_extract_string(payload, '$.data..roster.week')[1] as integer) as week,
        json_extract_string(payload, '$.data..team_key')[1]                   as team_key,
        json_extract_string(player, '$[*][*].player_key')[1]                  as player_key,

        -- Player, as Yahoo described them that week --------------------------
        -- player_id is the numeric tail of player_key, stable across seasons
        -- (player_key's game prefix changes every year). It is what a future
        -- cross-season or cross-source join would key on.
        cast(json_extract_string(player, '$[*][*].player_id')[1] as integer)  as yahoo_player_id,
        json_extract_string(player, '$[*][*].name.full')[1]                   as player_name,
        json_extract_string(player, '$[*][*].editorial_team_abbr')[1]         as nfl_team_abbr,
        json_extract_string(player, '$[*][*].display_position')[1]            as display_position,
        -- Every slot the player could legally fill, e.g. ['RB', 'W/R/T'].
        -- Kept as a list: the optimal-lineup logic needs the full set.
        json_extract_string(player, '$[*][*].eligible_positions[*].position') as eligible_positions,
        -- Yahoo's other identity for the same person, when it has one (two-way
        -- players). Null for everyone else.
        json_extract_string(player, '$[*][*].linked_player.player_key')[1]    as linked_player_key,

        -- That week's lineup and result --------------------------------------
        json_extract_string(player, '$[*].selected_position[*].position')[1]  as selected_position,
        -- Arrives as a string ('34.10'). decimal, not double: these get summed and
        -- compared against Yahoo's official team totals, and must match to the cent.
        cast(json_extract_string(player, '$[*].player_points.total')[1]
             as decimal(7, 2))                                                as fantasy_points,
        -- Whether any of the week's raw stats is non-zero. Yahoo reports a full
        -- stat line of zeros for a player who did not play (bye, inactive), so
        -- this separates "did not play" from "played and scored 0" -- the one
        -- case it cannot see is a player who was active but recorded nothing.
        coalesce(list_count(list_filter(
            json_extract_string(player, '$[*].player_stats.stats[*].stat.value'),
            lambda v: v not in ('0', '0.0', '-', ''))) > 0, false)                 as recorded_stats,

        -- Provenance ----------------------------------------------------------
        cast(payload ->> '$._meta.fetched_at' as timestamptz)                 as fetched_at

    from players

)

select * from renamed
