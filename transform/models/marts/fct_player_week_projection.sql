-- One row per player per week per league: the player's pre-game projected fantasy
-- points under that league's scoring. Grain: (league, season, week, player_id).
--
-- ESPN's projected stat components (stg_espn__player_projections) scored with the
-- league's weights (seed league_scoring), so a Yahoo league gets projections in its
-- own scoring too. This is the "spread" side of the matchup trend: a fantasy team's
-- projected total is its starters' projections, and how each slate's players beat
-- or missed theirs moves it. Offence only for now (kickers and defences have no
-- scoring weights yet).

with projections as (

    select * from {{ ref('stg_espn__player_projections') }}
    where player_id is not null

),

weights as (

    select league, pass_yd as w_pass_yd, pass_td as w_pass_td, rush_yd as w_rush_yd,
           rush_td as w_rush_td, rec_yd as w_rec_yd, reception as w_reception, rec_td as w_rec_td
    from {{ ref('league_scoring') }}

)

select
    weights.league,
    projections.season,
    projections.week,
    projections.player_id,
    projections.espn_id,
    round(
          coalesce(pass_yd, 0)   * w_pass_yd + coalesce(pass_td, 0) * w_pass_td
        + coalesce(rush_yd, 0)   * w_rush_yd + coalesce(rush_td, 0) * w_rush_td
        + coalesce(rec_yd, 0)    * w_rec_yd  + coalesce(reception, 0) * w_reception
        + coalesce(rec_td, 0)    * w_rec_td, 2)                        as projected_points,
    projections.target                                                 as projected_targets
from projections
cross join weights
