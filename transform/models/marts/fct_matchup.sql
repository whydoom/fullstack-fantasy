-- One row per league + week + team: that team's side of its head-to-head matchup.
-- Same grain as a team-week, so it is also where team-week measures that come from
-- the scoreboard (points for and against, the result) live.
--
-- team_points is the sum of the team's STARTED roster slots (fct_roster_slot), not
-- the scoreboard's total. The scoreboard figure is kept as reported_points. They
-- are meant to be equal, and nearly always are, but Yahoo's scoreboard API can
-- disagree with its own player points: ADN 2026 week 5, team 6, reported 126.20
-- while its starters summed to 124.20 -- and Yahoo's own matchup page showed 124.2.
-- The starters are what the site shows, so they are the score; the disagreement is
-- surfaced on every build by tests/assert_yahoo_reported_score_matches_starters.sql
-- (a warning), and a team-week with no roster rows at all is still an error
-- (tests/assert_yahoo_starters_match_team_score.sql).
--
-- The result (W/L/T) stays Yahoo's own winner_team_key: it is the official outcome,
-- and a 2-point reporting error rarely spans a margin. If it ever did, the warning
-- above would name the team-week.

with sides as (

    select * from {{ ref('stg_yahoo__matchup_teams') }}

),

starters as (

    select
        league_key,
        week,
        team_id,
        sum(fantasy_points)                                 as starter_points
    from {{ ref('fct_roster_slot') }}
    where is_starter
    group by all

),

scored as (

    select
        sides.*,
        cast(regexp_extract(sides.team_key, '\.t\.(\d+)$', 1) as integer)          as team_id,
        cast(regexp_extract(sides.opponent_team_key, '\.t\.(\d+)$', 1) as integer) as opponent_team_id,
        s.starter_points
    from sides
    left join starters as s
        on  s.league_key = sides.league_key
        and s.week       = sides.week
        and s.team_id    = cast(regexp_extract(sides.team_key, '\.t\.(\d+)$', 1) as integer)

),

with_opponent as (

    select
        scored.*,
        opp.starter_points                                  as opponent_starter_points
    from scored
    -- Every side's opponent is another side of the same league-week; the staging
    -- tests guarantee exactly one match.
    left join scored as opp
        on  opp.league_key = scored.league_key
        and opp.week       = scored.week
        and opp.team_key   = scored.opponent_team_key

)

select
    -- Grain -----------------------------------------------------------------------
    league_key,
    week,
    team_id,

    -- Context ---------------------------------------------------------------------
    season,
    opponent_team_id,
    week_start_date,
    week_end_date,
    is_playoffs,
    is_consolation,
    matchup_status = 'postevent'                                          as is_final,

    -- Result ------------------------------------------------------------------------
    -- Null until the week is final: a midevent "leader" is not a result, and
    -- counting it would put phantom wins into every in-progress standings table.
    case
        when matchup_status <> 'postevent' then null
        when is_tied                       then 'T'
        when winner_team_key = team_key    then 'W'
        else 'L'
    end                                                                   as result,

    -- Measures ----------------------------------------------------------------------
    starter_points                                                        as team_points,
    opponent_starter_points                                               as opponent_points,
    starter_points - opponent_starter_points                              as point_margin,
    projected_points,
    -- What Yahoo's scoreboard API reported; differs from team_points only when the
    -- API disagrees with its own player points (see header).
    team_points                                                           as reported_points

from with_opponent
