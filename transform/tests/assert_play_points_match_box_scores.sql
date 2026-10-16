-- Play-level fantasy points must add up to the weekly box score.
--
-- For every league and every player-week, the offensive points in fct_play_points
-- (return TDs aside: the box score files them separately) summed over the week must
-- equal nflverse's weekly box score scored with the same league weights. A miss
-- means a play is crediting the wrong player or dropping one -- the case that found
-- laterals (a pitch after the catch credits the second carrier, not the receiver).
-- Returns the offending player-weeks; passes when empty.

with weights as (

    select * from {{ ref('league_scoring') }}

),

plays as (

    select p.league, p.player_id, p.season, p.week,
           sum(p.offense_points) - sum(coalesce(p.return_td, 0)) * max(w.return_td) as pts
    from {{ ref('fct_play_points') }} p
    join weights w using (league)
    where p.season_type = 'REG'
    group by p.league, p.player_id, p.season, p.week

),

box as (

    select
        w.league, s.player_id, s.season, s.week,
          coalesce(s.passing_yards, 0) * w.pass_yd + coalesce(s.passing_tds, 0) * w.pass_td
        + coalesce(s.rushing_yards, 0) * w.rush_yd + coalesce(s.rushing_tds, 0) * w.rush_td
        + coalesce(s.receiving_yards, 0) * w.rec_yd + coalesce(s.receptions, 0) * w.reception
        + coalesce(s.receiving_tds, 0) * w.rec_td                                   as pts
    from {{ ref('stg_nflverse__player_stats_weekly') }} s
    cross join weights w
    where s.season_type = 'REG'
      and s.season >= (select min(season) from {{ ref('stg_nflverse__pbp') }})

)

select box.league, box.player_id, box.season, box.week,
       round(box.pts, 2) as box_points, round(coalesce(plays.pts, 0), 2) as play_points
from box
left join plays using (league, player_id, season, week)
where abs(coalesce(plays.pts, 0) - box.pts) >= 0.05
