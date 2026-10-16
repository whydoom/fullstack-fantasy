-- One row per player per play: every fantasy stat a player is credited with on one
-- play. Grain: (game_id, play_id, player_id).
--
-- Built long-then-wide: each kind of credit a play can give (a pass, a catch, a
-- carry, a return TD, a tackle, a sack ...) is one SELECT of (play, player, stat,
-- value); they are unioned and summed per player. A player who does two things on
-- one play -- intercepts and returns it for a TD, makes a tackle for loss that is
-- also a solo tackle -- gets one row with both.
--
-- Stats are raw counts and yards, not points: fct_play_points scores them per
-- league (seed league_scoring). Deleted plays are dropped; every other play stays.
--
-- Crediting, from nflverse's player columns:
--   targets / carries   opportunities, worth no points: every pass aimed at a
--               receiver (complete or not; sacks excluded) and every rush
--   passer      passing yards; a pass TD
--   receiver    receiving yards; a reception; a receiving TD (he scored it)
--   lateral     the player a receiver / rusher pitched to: his own yards and the
--               TD if he scored it (no reception)
--   rusher      rushing yards; a rushing TD (he scored it)
--   returner    a kick / punt return TD scored by the play's returner (on a punt
--               nflverse puts the returner on the "defense" side, so the returner
--               column -- not the team -- decides it)
--   defence     solo tackles; assists (assist_tackle_* and tackle_with_assist_*,
--               disjoint in the data); tackles for loss; sacks (half sacks 0.5);
--               interceptions; forced fumbles; fumble recoveries by the defence;
--               passes defended; blocked kicks; safeties; a defensive TD (an
--               interception or fumble return scored by a defender). Not for
--               the kicker on his own kick: his coverage tackle on a kickoff is
--               not fantasy points (the platforms' kicker scoring leaves it out)
--   kicker      a made field goal (and its distance, and which distance band),
--               a missed or blocked one; a good extra point, a failed or
--               blocked one

with plays as (

    select * from {{ ref('stg_nflverse__pbp') }}
    where not is_deleted

),

credits as (

    -- opportunities (no points; red-zone share and the targets table read them)
    select game_id, play_id, receiver_player_id as player_id, 'target' as stat, 1 as value
      from plays where receiver_player_id is not null and is_pass_attempt and not is_sack
    union all
    select game_id, play_id, rusher_player_id, 'carry', 1
      from plays where rusher_player_id is not null and is_rush_attempt

    -- offence
    union all
    select game_id, play_id, passer_player_id, 'pass_yd', passing_yards
      from plays where passer_player_id is not null and passing_yards is not null
    union all
    select game_id, play_id, passer_player_id, 'pass_td', 1
      from plays where passer_player_id is not null and is_pass_touchdown
    union all
    select game_id, play_id, receiver_player_id, 'rec_yd', receiving_yards
      from plays where receiver_player_id is not null and receiving_yards is not null
    union all
    select game_id, play_id, receiver_player_id, 'reception', 1
      from plays where receiver_player_id is not null and is_complete
    union all
    select game_id, play_id, receiver_player_id, 'rec_td', 1
      from plays where is_pass_touchdown and touchdown_player_id = receiver_player_id
    union all
    select game_id, play_id, rusher_player_id, 'rush_yd', rushing_yards
      from plays where rusher_player_id is not null and rushing_yards is not null
    union all
    select game_id, play_id, rusher_player_id, 'rush_td', 1
      from plays where is_rush_touchdown and touchdown_player_id = rusher_player_id
    -- laterals: the second carrier gets his own yards (no reception), and the TD
    -- if he scored it
    union all
    select game_id, play_id, lateral_receiver_player_id, 'rec_yd', lateral_receiving_yards
      from plays where lateral_receiver_player_id is not null and lateral_receiving_yards is not null
    union all
    select game_id, play_id, lateral_receiver_player_id, 'rec_td', 1
      from plays where is_pass_touchdown and touchdown_player_id = lateral_receiver_player_id
    union all
    select game_id, play_id, lateral_rusher_player_id, 'rush_yd', lateral_rushing_yards
      from plays where lateral_rusher_player_id is not null and lateral_rushing_yards is not null
    union all
    select game_id, play_id, lateral_rusher_player_id, 'rush_td', 1
      from plays where is_rush_touchdown and touchdown_player_id = lateral_rusher_player_id
    union all
    select game_id, play_id, touchdown_player_id, 'return_td', 1
      from plays where is_return_touchdown
        and touchdown_player_id in (kickoff_returner_player_id, punt_returner_player_id)

    -- defence
    union all
    select game_id, play_id, p, 'idp_tackle_solo', 1 from (
        select game_id, play_id, solo_tackle_1_player_id as p from plays
        union all select game_id, play_id, solo_tackle_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, p, 'idp_tackle_assist', 1 from (
        select game_id, play_id, assist_tackle_1_player_id as p from plays
        union all select game_id, play_id, assist_tackle_2_player_id from plays
        union all select game_id, play_id, assist_tackle_3_player_id from plays
        union all select game_id, play_id, assist_tackle_4_player_id from plays
        union all select game_id, play_id, tackle_with_assist_1_player_id from plays
        union all select game_id, play_id, tackle_with_assist_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, p, 'idp_tackle_for_loss', 1 from (
        select game_id, play_id, tackle_for_loss_1_player_id as p from plays
        union all select game_id, play_id, tackle_for_loss_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, sack_player_id, 'idp_sack', 1
      from plays where sack_player_id is not null
    union all
    select game_id, play_id, p, 'idp_sack', 0.5 from (
        select game_id, play_id, half_sack_1_player_id as p from plays
        union all select game_id, play_id, half_sack_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, interception_player_id, 'idp_interception', 1
      from plays where interception_player_id is not null
    union all
    select game_id, play_id, p, 'idp_fumble_forced', 1 from (
        select game_id, play_id, forced_fumble_player_1_player_id as p from plays
        union all select game_id, play_id, forced_fumble_player_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, fumble_recovery_1_player_id, 'idp_fumble_recovery', 1
      from plays where fumble_recovery_1_player_id is not null and fumble_recovery_1_team = defense_team
    union all
    select game_id, play_id, p, 'idp_pass_defended', 1 from (
        select game_id, play_id, pass_defense_1_player_id as p from plays
        union all select game_id, play_id, pass_defense_2_player_id from plays) where p is not null
    union all
    select game_id, play_id, blocked_player_id, 'idp_blocked_kick', 1
      from plays where blocked_player_id is not null
    union all
    select game_id, play_id, safety_player_id, 'idp_safety', 1
      from plays where safety_player_id is not null
    union all
    select game_id, play_id, touchdown_player_id, 'idp_touchdown', 1
      from plays where is_return_touchdown and touchdown_team = defense_team
        and touchdown_player_id is not null
        and touchdown_player_id not in (coalesce(punt_returner_player_id, ''), coalesce(kickoff_returner_player_id, ''))

    -- kicking
    union all
    select game_id, play_id, kicker_player_id, 'fg_made', 1
      from plays where kicker_player_id is not null and field_goal_result = 'made'
    union all
    select game_id, play_id, kicker_player_id, 'fg_yd', kick_distance
      from plays where kicker_player_id is not null and field_goal_result = 'made'
    union all
    select game_id, play_id, kicker_player_id,
           case when kick_distance >= 60 then 'fg_60_plus' when kick_distance >= 50 then 'fg_50_59'
                else 'fg_40_49' end, 1
      from plays where kicker_player_id is not null and field_goal_result = 'made' and kick_distance >= 40
    union all
    select game_id, play_id, kicker_player_id, 'fg_miss', 1
      from plays where kicker_player_id is not null and field_goal_result in ('missed', 'blocked')
    union all
    select game_id, play_id, kicker_player_id, 'xp_made', 1
      from plays where kicker_player_id is not null and extra_point_result = 'good'
    union all
    select game_id, play_id, kicker_player_id, 'xp_miss', 1
      from plays where kicker_player_id is not null and extra_point_result in ('failed', 'blocked')

),

-- the kicker's own coverage tackle on his kick is not a fantasy stat
kept as (

    select credits.*
    from credits
    join plays using (game_id, play_id)
    where not (credits.stat like 'idp_%' and credits.player_id = plays.kicker_player_id)

),

per_player as (

    select
        game_id,
        play_id,
        player_id,
        sum(value) filter (where stat = 'target')               as target,
        sum(value) filter (where stat = 'carry')                as carry,
        sum(value) filter (where stat = 'pass_yd')              as pass_yd,
        sum(value) filter (where stat = 'pass_td')              as pass_td,
        sum(value) filter (where stat = 'rush_yd')              as rush_yd,
        sum(value) filter (where stat = 'rush_td')              as rush_td,
        sum(value) filter (where stat = 'rec_yd')               as rec_yd,
        sum(value) filter (where stat = 'reception')            as reception,
        sum(value) filter (where stat = 'rec_td')               as rec_td,
        sum(value) filter (where stat = 'return_td')            as return_td,
        sum(value) filter (where stat = 'idp_tackle_solo')      as idp_tackle_solo,
        sum(value) filter (where stat = 'idp_tackle_assist')    as idp_tackle_assist,
        sum(value) filter (where stat = 'idp_tackle_for_loss')  as idp_tackle_for_loss,
        sum(value) filter (where stat = 'idp_sack')             as idp_sack,
        sum(value) filter (where stat = 'idp_interception')     as idp_interception,
        sum(value) filter (where stat = 'idp_fumble_forced')    as idp_fumble_forced,
        sum(value) filter (where stat = 'idp_fumble_recovery')  as idp_fumble_recovery,
        sum(value) filter (where stat = 'idp_pass_defended')    as idp_pass_defended,
        sum(value) filter (where stat = 'idp_blocked_kick')     as idp_blocked_kick,
        sum(value) filter (where stat = 'idp_safety')           as idp_safety,
        sum(value) filter (where stat = 'idp_touchdown')        as idp_touchdown,
        sum(value) filter (where stat = 'fg_made')              as fg_made,
        sum(value) filter (where stat = 'fg_yd')                as fg_yd,
        sum(value) filter (where stat = 'fg_40_49')             as fg_40_49,
        sum(value) filter (where stat = 'fg_50_59')             as fg_50_59,
        sum(value) filter (where stat = 'fg_60_plus')           as fg_60_plus,
        sum(value) filter (where stat = 'fg_miss')              as fg_miss,
        sum(value) filter (where stat = 'xp_made')              as xp_made,
        sum(value) filter (where stat = 'xp_miss')              as xp_miss
    from kept
    group by game_id, play_id, player_id

)

select
    per_player.*,
    -- the play, for context and the flags fct_play_points derives
    plays.season,
    plays.week,
    plays.season_type,
    plays.offense_team,
    plays.defense_team,
    plays.touchdown_team,
    plays.play_type,
    plays.play_description,
    plays.quarter,
    plays.quarter_seconds_remaining,
    plays.game_seconds_remaining,
    plays.yards_to_endzone,
    plays.offense_lead,
    plays.yards_gained,
    plays.is_touchdown,
    plays.is_pass_touchdown,
    plays.is_rush_touchdown,
    plays.is_return_touchdown,
    plays.passer_player_id,
    plays.receiver_player_id,
    plays.rusher_player_id,
    plays.interception_player_id,
    plays.forced_fumble_player_1_player_id,
    plays.kicker_player_id,
    plays.kick_distance,
    plays.fumble_recovery_1_player_id,
    plays.touchdown_player_id,
    plays.epa,
    plays.wpa
from per_player
join plays using (game_id, play_id)
