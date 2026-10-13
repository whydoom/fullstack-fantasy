{{ config(severity='warn') }}

-- For every final team-week, the points of the players fct_roster_slot marks as
-- started should equal the total Yahoo's scoreboard API reported, to the cent.
--
-- A WARNING, not an error: Yahoo's scoreboard can disagree with its own player
-- points (ADN 2026 week 5, team 6: reported 126.20, starters 124.20, and Yahoo's
-- matchup page showed 124.2). fct_matchup uses the starters, so the site is right
-- either way; this keeps every disagreement visible in each build's output
-- instead of letting it pass silently or stop the run.
--
-- Still worth reading when it fires: a disagreement on many team-weeks at once is
-- more likely a bug on our side (a wrong is_starter rule, a mis-parsed point
-- value) than Yahoo's. Returns the disagreeing team-weeks.

select
    league_key,
    week,
    team_id,
    team_points                         as starter_points,
    reported_points,
    reported_points - team_points       as reported_minus_starters
from {{ ref('fct_matchup') }}
where is_final
  and reported_points is distinct from team_points
