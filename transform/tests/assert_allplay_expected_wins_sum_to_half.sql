-- A week of head-to-head play hands out exactly n/2 wins (ties count half to each
-- side), so all-play expected wins must sum to n/2 every week. Any other total means
-- the all-play comparison is miscounting: a team compared against itself, a tie
-- double-counted, or a team missing from the week.
--
-- This is the runtime assertion from the ESPN pipeline's espn_luck.py, moved into
-- dbt so it runs on every build instead of only when the script does. Returns the
-- failing weeks.

select
    league_key,
    week,
    max(teams_in_week)      as teams_in_week,
    sum(expected_wins)      as expected_wins_total
from {{ ref('fct_team_week_allplay') }}
group by league_key, week
having abs(sum(expected_wins) - max(teams_in_week) / 2.0) > 1e-9
