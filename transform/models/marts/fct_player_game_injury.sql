-- One row per player per game in which the game stopped for his injury. Grain:
-- (game_id, player_id).
--
-- The official play-by-play logs both ends of an in-game injury in its play
-- descriptions, naming the player as TEAM-JERSEY-NAME:
--   "SEA-14-S.Darnold was injured during the play."
--   "** Injury Update: LAC-99-J.Caldwell has returned to the game."
-- Team + jersey + week find his player id (stg_nflverse__rosters_weekly).
--
--   injured_*        the first play he was hurt on: quarter, clock
--   has_return_note  the log says he returned
--   named_after      he is named on a later play of the game (carried, caught,
--                    threw, tackled) -- a return the log did not announce
--   snap_share       his share of his team's snaps that game (offence or defence,
--                    whichever he plays), and his usual_snap_share in his other
--                    games this season
--   did_not_return   no return note, never named again, and (when his usual share
--                    is known) a share well below it: hurt and out for the game
--   next_report_*    the following week's injury report: body part and status
--
-- Only injuries that stopped play are logged; a player who limps off between plays
-- is not here.

with plays as (

    select game_id, play_id, season, week, season_type, quarter, game_seconds_remaining,
           quarter_seconds_remaining, play_description
    from {{ ref('stg_nflverse__pbp') }}
    where play_description is not null

),

notes as (

    -- every "X was injured" / "X has returned" in a description
    select game_id, play_id, season, week, season_type, quarter, quarter_seconds_remaining,
           kind, regexp_extract(note, '^([A-Z]{2,3})-', 1) as team_abbr,
           cast(regexp_extract(note, '^[A-Z]{2,3}-(\d{1,2})-', 1) as integer) as jersey_number,
           regexp_extract(note, '^[A-Z]{2,3}-(\d{1,2}-.+?) (?:was injured|has returned)', 1) as jersey_name
    from (
        select *, 'injured' as kind,
               unnest(regexp_extract_all(play_description,
                      '[A-Z]{2,3}-\d{1,2}-[A-Za-z.'' -]+? was injured')) as note
        from plays where play_description like '%was injured%'
        union all
        select *, 'returned',
               unnest(regexp_extract_all(play_description,
                      '[A-Z]{2,3}-\d{1,2}-[A-Za-z.'' -]+? has returned'))
        from plays where play_description like '%has returned%'
    )

),

rosters as (

    select season, week, team_abbr, jersey_number, min(player_id) as player_id
    from {{ ref('stg_nflverse__rosters_weekly') }}
    group by all

),

hurt as (

    select n.*, r.player_id
    from notes n
    join rosters r using (season, week, team_abbr, jersey_number)

),

first_injury as (

    select game_id, player_id, season, week, season_type, team_abbr, jersey_name,
           arg_min(play_id, play_id)                    as injured_play_id,
           arg_min(quarter, play_id)                    as injured_quarter,
           cast(arg_min(quarter_seconds_remaining, play_id) as integer) as injured_quarter_seconds
    from hurt
    where kind = 'injured'
    group by all

),

returned as (

    select f.game_id, f.player_id,
           bool_or(h.kind = 'returned' and h.play_id > f.injured_play_id) as has_return_note
    from first_injury f
    left join hurt h using (game_id, player_id)
    group by all

),

named_after as (

    -- named on a later play, other than in an injury note: "14-S.Darnold pass ..."
    select f.game_id, f.player_id, count(p.play_id) > 0 as named_after
    from first_injury f
    left join plays p
      on p.game_id = f.game_id and p.play_id > f.injured_play_id
     and p.play_description like '%' || f.jersey_name || '%'
     and p.play_description not like '%' || f.jersey_name || ' was injured%'
     and p.play_description not like '%' || f.jersey_name || ' has returned%'
    group by all

),

snaps as (

    select s.game_id, x.gsis_id as player_id,
           greatest(coalesce(s.offense_snap_share, 0), coalesce(s.defense_snap_share, 0)) as snap_share
    from {{ ref('stg_nflverse__snap_counts') }} s
    join (select pfr_id, min(gsis_id) as gsis_id from {{ source('raw_nflverse', 'player_id_crosswalk') }}
          where pfr_id is not null and gsis_id is not null group by 1) x
      on x.pfr_id = s.pfr_player_id

),

usual as (

    select f.game_id, f.player_id, avg(s.snap_share) as usual_snap_share
    from first_injury f
    join snaps s on s.player_id = f.player_id and s.game_id <> f.game_id and s.snap_share > 0
    group by all

),

reports as (

    select season, week, player_id, report_primary_injury, report_status
    from {{ ref('stg_nflverse__injuries') }}

)

select
    f.game_id,
    f.player_id,
    f.season,
    f.week,
    f.season_type,
    f.team_abbr,
    f.injured_quarter,
    f.injured_quarter_seconds,
    printf('%d:%02d', f.injured_quarter_seconds // 60, f.injured_quarter_seconds % 60) as injured_clock,
    coalesce(r.has_return_note, false)                                as has_return_note,
    n.named_after,
    s.snap_share,
    u.usual_snap_share,
    not coalesce(r.has_return_note, false) and not n.named_after
        and (u.usual_snap_share is null or s.snap_share is null
             or s.snap_share < 0.8 * u.usual_snap_share)              as did_not_return,
    nr.report_primary_injury                                          as next_report_injury,
    nr.report_status                                                  as next_report_status
from first_injury f
left join returned r using (game_id, player_id)
left join named_after n using (game_id, player_id)
left join snaps s using (game_id, player_id)
left join usual u using (game_id, player_id)
left join reports nr on nr.player_id = f.player_id and nr.season = f.season and nr.week = f.week + 1
