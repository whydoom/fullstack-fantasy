-- One row per league + week + team, for final regular-season weeks: how that week's
-- score would have fared against every other team, not just the scheduled opponent.
-- The schedule-luck section of the site is built from these rows.
--
--   all-play      beat (n - r) of the other (n - 1) teams if you scored rank r of n
--   expected win  (beaten + half of tied) / (n - 1): that week's share of a win
--                 under a neutral schedule
--   vs median     cleared the week's median score
--   close game    decided by <= close_game_margin points
--
-- Same definitions as the ESPN pipeline's espn_luck.py, so the two leagues' luck
-- tables are comparable. Its runtime assertion (each week's expected wins sum to
-- n/2, since a week hands out exactly n/2 real wins) is a dbt test here:
-- tests/assert_allplay_expected_wins_sum_to_half.sql.
--
-- Only final, regular-season weeks: an in-progress score is not a result, and a
-- playoff week has fewer teams playing, which would break the n/2 identity.

{% set close_game_margin = var('close_game_margin', 5.0) %}

with weeks as (

    select *
    from {{ ref('fct_matchup') }}
    where is_final and not is_playoffs and not is_consolation

),

scored as (

    select
        w.league_key,
        w.week,
        w.team_id,
        w.team_points,
        w.point_margin,
        count(*) over (partition by w.league_key, w.week)                 as teams_in_week,
        median(w.team_points) over (partition by w.league_key, w.week)    as week_median_points
    from weeks as w

),

allplay as (

    select
        s.league_key,
        s.week,
        s.team_id,
        count(*) filter (where o.team_points < s.team_points)             as allplay_wins,
        count(*) filter (where o.team_points = s.team_points)             as allplay_ties,
        count(*) filter (where o.team_points > s.team_points)             as allplay_losses
    from scored as s
    join scored as o
        on  o.league_key = s.league_key
        and o.week       = s.week
        and o.team_id   <> s.team_id
    group by all

)

select
    -- Grain ---------------------------------------------------------------------
    s.league_key,
    s.week,
    s.team_id,

    -- Context -------------------------------------------------------------------
    s.teams_in_week,
    s.team_points,
    s.week_median_points,

    -- Measures ------------------------------------------------------------------
    a.allplay_wins,
    a.allplay_ties,
    a.allplay_losses,
    (a.allplay_wins + 0.5 * a.allplay_ties) / (s.teams_in_week - 1)       as expected_wins,
    s.team_points > s.week_median_points                                  as beat_median,
    abs(s.point_margin) <= {{ close_game_margin }}                        as is_close_game

from scored as s
join allplay as a
    using (league_key, week, team_id)
