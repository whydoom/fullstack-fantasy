-- Play-level fantasy points must add up to the weekly box score.
--
-- For every league and every player-week, the offensive points in fct_play_points
-- summed over the week must equal nflverse's weekly box score scored with the same
-- league weights: yards, receptions, touchdowns, interceptions, lost fumbles and
-- 2-point conversions. Return TDs, pick sixes and long-play bonuses are left out of
-- both sides: the box score does not count them. A miss
-- means a play is crediting the wrong player or dropping one -- the case that found
-- laterals (a pitch after the catch credits the second carrier, not the receiver).
-- Returns the offending player-weeks; passes when empty.
--
-- Left out: a player the week's play-by-play never names as passer, rusher, receiver
-- or lateral carrier. nflverse keeps one lateral carrier per play, so in a long
-- lateral chain the middle carriers exist only in the box score (2026 week 10, SF at
-- DAL, last play: Cooper Beebe's -6 yards on the second of five laterals). No play can
-- credit him, so the miss is nflverse's, not a play crediting the wrong player.

with weights as (

    select * from {{ ref('league_scoring') }}

),

plays as (

    -- built from the stat components, so the long-play bonuses (ADN's 40+ yards)
    -- and pick sixes -- which the box score does not count -- stay out of both sides
    select p.league, p.player_id, p.season, p.week,
          sum(coalesce(p.pass_yd, 0)) * max(w.pass_yd) + sum(coalesce(p.pass_td, 0)) * max(w.pass_td)
        + sum(coalesce(p.rush_yd, 0)) * max(w.rush_yd) + sum(coalesce(p.rush_td, 0)) * max(w.rush_td)
        + sum(coalesce(p.rec_yd, 0)) * max(w.rec_yd) + sum(coalesce(p.reception, 0)) * max(w.reception)
        + sum(coalesce(p.rec_td, 0)) * max(w.rec_td)
        + sum(coalesce(p.pass_int, 0)) * max(w.pass_int) + sum(coalesce(p.fumble_lost, 0)) * max(w.fumble_lost)
        + sum(coalesce(p.two_pt_pass, 0)) * max(w.two_pt_pass) + sum(coalesce(p.two_pt_rush, 0)) * max(w.two_pt_rush)
        + sum(coalesce(p.two_pt_rec, 0)) * max(w.two_pt_rec)                              as pts
    from {{ ref('fct_play_points') }} p
    join weights w using (league)
    where p.season_type = 'REG'
    group by p.league, p.player_id, p.season, p.week

),

named as (

    select distinct season, week, player_id
    from {{ ref('stg_nflverse__pbp') }}
    unpivot (player_id for role in (passer_player_id, rusher_player_id, receiver_player_id,
                                     lateral_rusher_player_id, lateral_receiver_player_id))
    where player_id is not null

),

box as (

    select
        w.league, s.player_id, s.season, s.week,
          coalesce(s.passing_yards, 0) * w.pass_yd + coalesce(s.passing_tds, 0) * w.pass_td
        + coalesce(s.rushing_yards, 0) * w.rush_yd + coalesce(s.rushing_tds, 0) * w.rush_td
        + coalesce(s.receiving_yards, 0) * w.rec_yd + coalesce(s.receptions, 0) * w.reception
        + coalesce(s.receiving_tds, 0) * w.rec_td
        + coalesce(s.passing_interceptions, 0) * w.pass_int
        + (coalesce(s.rushing_fumbles_lost, 0) + coalesce(s.receiving_fumbles_lost, 0)
           + coalesce(s.sack_fumbles_lost, 0)) * w.fumble_lost
        + coalesce(s.passing_2pt_conversions, 0) * w.two_pt_pass
        + coalesce(s.rushing_2pt_conversions, 0) * w.two_pt_rush
        + coalesce(s.receiving_2pt_conversions, 0) * w.two_pt_rec                   as pts
    from {{ ref('stg_nflverse__player_stats_weekly') }} s
    cross join weights w
    where s.season_type = 'REG'
      and s.season >= (select min(season) from {{ ref('stg_nflverse__pbp') }})

)

select box.league, box.player_id, box.season, box.week,
       round(box.pts, 2) as box_points, round(coalesce(plays.pts, 0), 2) as play_points
from box
join named using (player_id, season, week)
left join plays using (league, player_id, season, week)
where abs(coalesce(plays.pts, 0) - box.pts) >= 0.05
