-- One row per player per game: what he actually produced and what his usage was
-- expected to produce ("expected fantasy points" -- where his targets and carries
-- came on the field, not a pre-game projection). Grain: (game_id, player_id).
--
-- Kept as stat components, actual and expected, so a mart can score both under each
-- league's own rules (seed league_scoring) rather than nflverse's default scoring.
-- Rows with no player are dropped: nflverse emits one per team per game with nothing
-- in it, and they would break the grain.

select
    game_id,
    season,
    cast(week as integer)       as week,
    posteam                     as team_abbr,
    player_id,
    full_name                   as player_name,
    position,

    -- usage ------------------------------------------------------------------
    pass_attempt,
    rec_attempt                 as targets,
    rush_attempt                as carries,

    -- actual ------------------------------------------------------------------
    pass_completions,
    pass_yards_gained,
    pass_touchdown,
    pass_interception,
    receptions,
    rec_yards_gained,
    rec_touchdown,
    rush_yards_gained,
    rush_touchdown,

    -- expected, from usage ----------------------------------------------------
    pass_completions_exp,
    pass_yards_gained_exp,
    pass_touchdown_exp,
    pass_interception_exp,
    receptions_exp,
    rec_yards_gained_exp,
    rec_touchdown_exp,
    rush_yards_gained_exp,
    rush_touchdown_exp,

    -- nflverse's own scoring, kept for reference ---------------------------------
    total_fantasy_points,
    total_fantasy_points_exp

from {{ source('raw_nflverse', 'ff_opportunity_weekly') }}
where player_id is not null
