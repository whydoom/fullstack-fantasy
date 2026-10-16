-- One row per charted play (FTN Data, via nflverse). Grain: (game_id, play_id),
-- matching stg_nflverse__pbp's key so the two join one to one. Rename and type
-- only. Kept: the pass-quality flags the target summary needs (drops, catchable,
-- contested) and the play-design flags (play action, screens, RPO, blitz counts).

select
    nflverse_game_id                        as game_id,
    cast(nflverse_play_id as integer)       as play_id,
    season,
    week,
    cast(is_drop as boolean)                as is_drop,
    cast(is_catchable_ball as boolean)      as is_catchable,
    cast(is_contested_ball as boolean)      as is_contested,
    cast(is_created_reception as boolean)   as is_created_reception,
    cast(is_throw_away as boolean)          as is_throw_away,
    cast(is_interception_worthy as boolean) as is_interception_worthy,
    cast(is_play_action as boolean)         as is_play_action,
    cast(is_screen_pass as boolean)         as is_screen_pass,
    cast(is_rpo as boolean)                 as is_rpo,
    cast(is_motion as boolean)              as is_motion,
    cast(is_no_huddle as boolean)           as is_no_huddle,
    cast(is_qb_out_of_pocket as boolean)    as is_qb_out_of_pocket,
    n_blitzers,
    n_pass_rushers,
    n_defense_box

from {{ source('raw_nflverse', 'ftn_charting') }}
