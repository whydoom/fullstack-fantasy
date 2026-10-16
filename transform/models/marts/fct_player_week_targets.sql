-- One row per player per game he was targeted: what became of his targets.
-- Grain: (game_id, player_id).
--
--   targets              passes aimed at him (fct_play_player, sacks excluded)
--   catches              completed
--   drops                FTN charted the incompletion as his drop
--   intercepted          picked off
--   other_incompletions  the rest: off target, defended, thrown away at him --
--                        targets minus catches, drops and interceptions
--   catchable, contested FTN's read of the ball
--   charted_targets      how many of his targets FTN has charted; FTN runs about a
--                        day behind, so a fresh week reads drops as 0 until it is
--                        charted -- check this before calling a week drop-free

with targets as (

    select game_id, play_id, player_id, season, week, season_type, reception
    from {{ ref('fct_play_player') }}
    where target > 0

),

plays as (

    select game_id, play_id, is_interception from {{ ref('stg_nflverse__pbp') }}

),

ftn as (

    select game_id, play_id, is_drop, is_catchable, is_contested
    from {{ ref('stg_nflverse__ftn_charting') }}

),

per_target as (

    select
        targets.*,
        plays.is_interception,
        ftn.play_id is not null                         as is_charted,
        coalesce(ftn.is_drop, false)
            and coalesce(targets.reception, 0) = 0      as is_drop,
        coalesce(ftn.is_catchable, false)               as is_catchable,
        coalesce(ftn.is_contested, false)               as is_contested
    from targets
    join plays using (game_id, play_id)
    left join ftn using (game_id, play_id)

)

select
    game_id,
    player_id,
    season,
    week,
    season_type,
    count(*)                                            as targets,
    sum(coalesce(reception, 0))                         as catches,
    count(*) filter (where is_drop)                     as drops,
    count(*) filter (where is_interception)             as intercepted,
    count(*) - sum(coalesce(reception, 0))
        - count(*) filter (where is_drop)
        - count(*) filter (where is_interception and not is_drop)
                                                        as other_incompletions,
    count(*) filter (where is_catchable)                as catchable,
    count(*) filter (where is_contested)                as contested,
    count(*) filter (where is_charted)                  as charted_targets
from per_target
group by game_id, player_id, season, week, season_type
