-- One row per player per game per league: the fantasy points his usage was worth
-- (expected) against what he scored, both under the league's scoring.
-- Grain: (league, game_id, player_id).
--
-- "Expected" is nflverse's ffopportunity model: what an average player would score
-- from the same targets and carries, given where on the field and how far downfield
-- they came. It is a judgment of the touches, not a pre-game projection. Both sides
-- are scored from the same stat components with the league's weights (seed
-- league_scoring), so the difference is pure over- / under-performance:
--
--   points_over_expected > 0   he turned his touches into more than they were worth
--                              (efficiency, or luck: a long TD on a short catch)
--   points_over_expected < 0   the touches were there, the points were not

with usage as (

    select * from {{ ref('stg_nflverse__ff_opportunity_weekly') }}

),

weights as (

    select league, pass_yd, pass_td, rush_yd, rush_td, rec_yd, reception, rec_td
    from {{ ref('league_scoring') }}

),

scored as (

    select
        weights.league,
        usage.game_id,
        usage.player_id,
        usage.player_name,
        usage.position,
        usage.team_abbr,
        usage.season,
        usage.week,
        usage.targets,
        usage.carries,
          coalesce(usage.pass_yards_gained, 0) * weights.pass_yd
        + coalesce(usage.pass_touchdown, 0)    * weights.pass_td
        + coalesce(usage.rush_yards_gained, 0) * weights.rush_yd
        + coalesce(usage.rush_touchdown, 0)    * weights.rush_td
        + coalesce(usage.rec_yards_gained, 0)  * weights.rec_yd
        + coalesce(usage.receptions, 0)        * weights.reception
        + coalesce(usage.rec_touchdown, 0)     * weights.rec_td        as actual_points,
          coalesce(usage.pass_yards_gained_exp, 0) * weights.pass_yd
        + coalesce(usage.pass_touchdown_exp, 0)    * weights.pass_td
        + coalesce(usage.rush_yards_gained_exp, 0) * weights.rush_yd
        + coalesce(usage.rush_touchdown_exp, 0)    * weights.rush_td
        + coalesce(usage.rec_yards_gained_exp, 0)  * weights.rec_yd
        + coalesce(usage.receptions_exp, 0)        * weights.reception
        + coalesce(usage.rec_touchdown_exp, 0)     * weights.rec_td    as expected_points
    from usage
    cross join weights

)

select
    *,
    round(actual_points - expected_points, 2)                          as points_over_expected
from scored
