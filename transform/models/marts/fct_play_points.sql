-- One row per player per play per fantasy league: the fantasy points that play was
-- worth to that player under the league's scoring (seed league_scoring), with the
-- flags the recap reads. Grain: (league, game_id, play_id, player_id).
--
-- Only rows worth something (or with a touchdown) are kept: a 0-point tackle in a
-- league without IDP scoring is not a fantasy play. Rosters are joined later, per
-- league, outside dbt (plays_export.py) -- ESPN rosters are not in the warehouse --
-- so this stays a statement about NFL players.
--
-- Flags:
--   is_garbage_time  4th quarter, under 10:00 left, the margin 17+ either way
--                    (three scores): points that did not matter to the real game
--   is_red_zone      the offence snapped the ball inside the opponent's 20
--   scored_td        the player scored a touchdown on the play (any kind)
--   is_vulture       ... from 3 yards or closer (rushing / receiving)
--   is_big_play      ... from 40+ yards
--   player_yards     his own yards on the play (rush + receiving, or passing for a
--                    passer)

with credits as (

    select * from {{ ref('fct_play_player') }}

),

scoring as (

    -- the per-stat weights, prefixed w_ so they never collide with the stat columns
    select
        league,
        pass_yd as w_pass_yd,
        pass_td as w_pass_td,
        rush_yd as w_rush_yd,
        rush_td as w_rush_td,
        rec_yd as w_rec_yd,
        reception as w_reception,
        rec_td as w_rec_td,
        return_td as w_return_td,
        idp_tackle_solo as w_idp_tackle_solo,
        idp_tackle_assist as w_idp_tackle_assist,
        idp_tackle_for_loss as w_idp_tackle_for_loss,
        idp_sack as w_idp_sack,
        idp_interception as w_idp_interception,
        idp_fumble_forced as w_idp_fumble_forced,
        idp_fumble_recovery as w_idp_fumble_recovery,
        idp_pass_defended as w_idp_pass_defended,
        idp_blocked_kick as w_idp_blocked_kick,
        idp_safety as w_idp_safety,
        idp_touchdown as w_idp_touchdown
    from {{ ref('league_scoring') }}

),

scored as (

    select
        scoring.league,
        credits.*,
        coalesce(pass_yd, 0) * scoring.w_pass_yd
            + coalesce(pass_td, 0) * scoring.w_pass_td
            + coalesce(rush_yd, 0) * scoring.w_rush_yd
            + coalesce(rush_td, 0) * scoring.w_rush_td
            + coalesce(rec_yd, 0) * scoring.w_rec_yd
            + coalesce(reception, 0) * scoring.w_reception
            + coalesce(rec_td, 0) * scoring.w_rec_td
            + coalesce(return_td, 0) * scoring.w_return_td               as offense_points,
        coalesce(idp_tackle_solo, 0) * scoring.w_idp_tackle_solo
            + coalesce(idp_tackle_assist, 0) * scoring.w_idp_tackle_assist
            + coalesce(idp_tackle_for_loss, 0) * scoring.w_idp_tackle_for_loss
            + coalesce(idp_sack, 0) * scoring.w_idp_sack
            + coalesce(idp_interception, 0) * scoring.w_idp_interception
            + coalesce(idp_fumble_forced, 0) * scoring.w_idp_fumble_forced
            + coalesce(idp_fumble_recovery, 0) * scoring.w_idp_fumble_recovery
            + coalesce(idp_pass_defended, 0) * scoring.w_idp_pass_defended
            + coalesce(idp_blocked_kick, 0) * scoring.w_idp_blocked_kick
            + coalesce(idp_safety, 0) * scoring.w_idp_safety
            + coalesce(idp_touchdown, 0) * scoring.w_idp_touchdown       as defense_points
    from credits
    cross join scoring

)

select
    league,
    game_id,
    play_id,
    player_id,
    season,
    week,
    season_type,
    round(offense_points + defense_points, 2)                        as points,
    round(offense_points, 2)                                         as offense_points,
    round(defense_points, 2)                                         as defense_points,
    coalesce(rush_yd, 0) + coalesce(rec_yd, 0)                       as scrimmage_yards,
    coalesce(pass_yd, 0)                                             as pass_yards,
    case when coalesce(pass_yd, 0) > 0 and coalesce(rush_yd, 0) + coalesce(rec_yd, 0) = 0
         then coalesce(pass_yd, 0) else coalesce(rush_yd, 0) + coalesce(rec_yd, 0) end
                                                                     as player_yards,
    coalesce(rush_td, 0) + coalesce(rec_td, 0) + coalesce(return_td, 0)
        + coalesce(idp_touchdown, 0) > 0                             as scored_td,
    coalesce(pass_td, 0) > 0                                         as threw_td,
    (coalesce(rush_td, 0) + coalesce(rec_td, 0) > 0)
        and coalesce(rush_yd, 0) + coalesce(rec_yd, 0) <= 3          as is_vulture,
    (coalesce(rush_td, 0) + coalesce(rec_td, 0) + coalesce(return_td, 0)
        + coalesce(idp_touchdown, 0) + coalesce(pass_td, 0) > 0)
        and yards_gained >= 40                                       as is_big_play,
    quarter = 4 and quarter_seconds_remaining < 600
        and abs(offense_lead) >= 17                                  as is_garbage_time,
    play_type in ('pass', 'run') and yards_to_endzone <= 20          as is_red_zone,
    -- what the player did, for the defensive-TD bullets
    coalesce(idp_touchdown, 0) > 0
        and (player_id = interception_player_id
             or (player_id = forced_fumble_player_1_player_id
                 and player_id = fumble_recovery_1_player_id))       as is_self_made_defensive_td,
    coalesce(return_td, 0) > 0                                       as is_return_td,
    coalesce(idp_touchdown, 0) > 0                                   as is_defensive_td,
    -- the play
    offense_team,
    defense_team,
    touchdown_team,
    play_type,
    play_description,
    quarter,
    game_seconds_remaining,
    yards_gained,
    yards_to_endzone,
    offense_lead,
    epa,
    wpa,
    -- stat components, for summaries
    target, carry, pass_yd, pass_td, rush_yd, rush_td, rec_yd, reception, rec_td, return_td,
    idp_tackle_solo, idp_tackle_assist, idp_tackle_for_loss, idp_sack, idp_interception,
    idp_fumble_forced, idp_fumble_recovery, idp_pass_defended, idp_blocked_kick, idp_safety,
    idp_touchdown
from scored
where offense_points + defense_points <> 0
   or coalesce(rush_td, 0) + coalesce(rec_td, 0) + coalesce(return_td, 0) + coalesce(idp_touchdown, 0) > 0
   or (play_type in ('pass', 'run') and yards_to_endzone <= 20
       and coalesce(target, 0) + coalesce(carry, 0) > 0)
