-- One row per league + week + team: that team's side of its head-to-head matchup.
-- Same grain as a team-week, so it is also where team-week measures that come from
-- the scoreboard (points for and against, the result) live.
--
-- team_points is Yahoo's official score, taken from the scoreboard, not re-derived
-- from fct_roster_slot. Keeping the two independent is the point: the singular test
-- tests/assert_yahoo_starters_match_team_score.sql sums started roster slots and
-- requires the result to equal this number, so the roster fact is checked against
-- Yahoo's own arithmetic rather than against itself.

with sides as (

    select * from {{ ref('stg_yahoo__matchup_teams') }}

),

with_opponent as (

    select
        sides.*,
        opp.team_points                                     as opponent_points
    from sides
    -- Every side's opponent is another side of the same league-week; the staging
    -- tests guarantee exactly one match.
    left join sides as opp
        on  opp.league_key = sides.league_key
        and opp.week       = sides.week
        and opp.team_key   = sides.opponent_team_key

)

select
    -- Grain -----------------------------------------------------------------------
    league_key,
    week,
    cast(regexp_extract(team_key, '\.t\.(\d+)$', 1) as integer)           as team_id,

    -- Context ---------------------------------------------------------------------
    season,
    cast(regexp_extract(opponent_team_key, '\.t\.(\d+)$', 1) as integer)  as opponent_team_id,
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
    team_points,
    opponent_points,
    team_points - opponent_points                                         as point_margin,
    projected_points

from with_opponent
