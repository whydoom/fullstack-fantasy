-- One row per player per game he played offence or touched the ball: how much of
-- his team's offence ran through him. Grain: (game_id, player_id).
--
--   offense_snaps / snap_share   PFR snap counts (who was on the field play by
--                                play is not published for current seasons; the
--                                per-game share is), matched to the GSIS id
--                                through the crosswalk's pfr_id
--   targets / target_share       his targets over his team's targeted passes
--   carries / carry_share        his carries over his team's carries
--   is_workhorse                 on the field for 70%+ of his team's offensive
--                                snaps

with plays as (

    select * from {{ ref('fct_play_player') }}

),

touches as (

    select game_id, player_id, offense_team as team_abbr, season, week, season_type,
           sum(coalesce(target, 0)) as targets,
           sum(coalesce(carry, 0))  as carries
    from plays
    where coalesce(target, 0) + coalesce(carry, 0) > 0
    group by game_id, player_id, offense_team, season, week, season_type

),

team_touches as (

    select game_id, team_abbr, sum(targets) as team_targets, sum(carries) as team_carries
    from touches
    group by game_id, team_abbr

),

crosswalk as (

    -- one gsis id per PFR id
    select pfr_id, min(gsis_id) as gsis_id
    from {{ source('raw_nflverse', 'player_id_crosswalk') }}
    where pfr_id is not null and gsis_id is not null
    group by pfr_id

),

snaps as (

    select s.game_id, x.gsis_id as player_id, s.team_abbr, s.season, s.week, s.season_type,
           s.offense_snaps, s.offense_snap_share
    from {{ ref('stg_nflverse__snap_counts') }} s
    join crosswalk x on x.pfr_id = s.pfr_player_id
    where s.offense_snaps > 0

),

players as (

    select game_id, player_id, team_abbr, season, week, season_type from touches
    union
    select game_id, player_id, team_abbr, season, week, season_type from snaps

)

select
    players.game_id,
    players.player_id,
    players.team_abbr,
    players.season,
    players.week,
    players.season_type,
    coalesce(snaps.offense_snaps, 0)                                as offense_snaps,
    snaps.offense_snap_share                                        as snap_share,
    coalesce(touches.targets, 0)                                    as targets,
    round(coalesce(touches.targets, 0) / nullif(team_touches.team_targets, 0), 3)
                                                                    as target_share,
    coalesce(touches.carries, 0)                                    as carries,
    round(coalesce(touches.carries, 0) / nullif(team_touches.team_carries, 0), 3)
                                                                    as carry_share,
    coalesce(snaps.offense_snap_share, 0) >= 0.70                   as is_workhorse
from players
left join touches using (game_id, player_id)
left join snaps using (game_id, player_id)
left join team_touches
       on team_touches.game_id = players.game_id
      and team_touches.team_abbr = players.team_abbr
