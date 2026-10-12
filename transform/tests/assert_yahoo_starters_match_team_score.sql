-- Ties the two Yahoo facts together through Yahoo's own arithmetic: for every final
-- team-week, the points of the players fct_roster_slot marks as started must sum to
-- the official score fct_matchup took from the scoreboard, to the cent.
--
-- Catches, in one place: a wrong is_starter rule (BN/IR leaking in or a real slot
-- left out), missing or duplicated roster rows, a mis-parsed point value, and a
-- roster/scoreboard landing mismatch. Both sides are decimal, so equality is exact.
--
-- Gaps fail too (full outer join): a final team-week with no roster rows, or roster
-- rows with no matchup at all, so a landing gap cannot pass by simply having nothing
-- to compare. Scores are compared only for final weeks: mid-week, Yahoo's
-- scoreboard and roster points update independently. Returns the failing team-weeks.

with started as (

    select league_key, week, team_id, sum(fantasy_points) as starter_points
    from {{ ref('fct_roster_slot') }}
    where is_starter
    group by all

),

official as (

    select league_key, week, team_id, team_points, is_final
    from {{ ref('fct_matchup') }}

)

select
    coalesce(o.league_key, s.league_key)  as league_key,
    coalesce(o.week, s.week)              as week,
    coalesce(o.team_id, s.team_id)        as team_id,
    s.starter_points,
    o.team_points
from official as o
full outer join started as s
    on  s.league_key = o.league_key
    and s.week       = o.week
    and s.team_id    = o.team_id
where o.team_id is null                                              -- roster, no matchup
   or (o.is_final and s.starter_points is distinct from o.team_points)  -- wrong or missing
