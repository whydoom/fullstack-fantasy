-- Atomic fact on the Yahoo side: one row per league + week + team + player.
-- Every Yahoo team-week measure (actual score, optimal score, bench points) is a
-- rollup of these rows, so this is where the one business rule they share lives:
-- what counts as "started".
--
-- Keys follow the dim_team_season contract (league_key + team_id) rather than
-- Yahoo's concatenated team_key, so a team joins to its dimension on the same
-- composite every other Yahoo fact will use.
--
-- Player descriptors (name, NFL team, display position) are deliberately NOT here.
-- They describe the player, not the roster event, and belong in a player dimension.
-- dim_player is nflverse-keyed today; conforming Yahoo players into it is separate
-- work, so for now the fact carries yahoo_player_id, the cross-season-stable id that
-- conformance will join on.
--
-- eligible_positions IS kept: it is the player's eligibility as of that week, which
-- is what decides whether a different lineup was legal that week. Yahoo can change
-- eligibility mid-season, so it is a property of the week, not of the player.

with slots as (

    select * from {{ ref('stg_yahoo__roster_slots') }}

)

select
    -- Grain ---------------------------------------------------------------------
    league_key,
    week,
    cast(regexp_extract(team_key, '\.t\.(\d+)$', 1) as integer)  as team_id,
    player_key,

    -- Context -------------------------------------------------------------------
    season,
    yahoo_player_id,
    selected_position,
    eligible_positions,

    -- The business rule --------------------------------------------------------
    -- Bench (BN) and injured reserve (IR) score for no one. Every other slot,
    -- including flex (W/R/T) and IDP (D), counts toward the team's total. Verified:
    -- summing fantasy_points over is_starter reproduces Yahoo's official team score
    -- for all 48 team-weeks landed (tests/assert_yahoo_starters_match_team_score.sql
    -- will pin this once the scoreboard is staged).
    selected_position not in ('BN', 'IR')                         as is_starter,

    -- Measure -------------------------------------------------------------------
    -- Points the player scored that week, whether or not they started. Additive
    -- across players; sum it filtered on is_starter for the team's actual score,
    -- unfiltered for "everyone on the roster".
    fantasy_points

from slots
