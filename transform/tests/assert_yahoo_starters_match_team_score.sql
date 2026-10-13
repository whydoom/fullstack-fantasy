-- Coverage check between the two Yahoo facts: every final team-week in fct_matchup
-- has started roster slots in fct_roster_slot, and every team-week with roster
-- slots has a matchup. A gap means a roster or scoreboard file failed to land, so
-- this is an error.
--
-- It used to also require the starters to sum to the scoreboard's total. Since
-- fct_matchup.team_points IS that sum, the comparison now lives in
-- assert_yahoo_reported_score_matches_starters.sql, against the scoreboard's own
-- reported figure, as a warning (Yahoo's API can disagree with itself).
-- Returns the team-weeks missing on either side.

with started as (

    select distinct league_key, week, team_id
    from {{ ref('fct_roster_slot') }}
    where is_starter

),

official as (

    select league_key, week, team_id, is_final
    from {{ ref('fct_matchup') }}

)

select
    coalesce(o.league_key, s.league_key)  as league_key,
    coalesce(o.week, s.week)              as week,
    coalesce(o.team_id, s.team_id)        as team_id,
    case when o.team_id is null then 'roster rows, no matchup'
         else 'final matchup, no roster rows' end as gap
from official as o
full outer join started as s
    on  s.league_key = o.league_key
    and s.week       = o.week
    and s.team_id    = o.team_id
where o.team_id is null
   or (o.is_final and s.team_id is null)
