-- One row per play (play-by-play), the current season on. Grain: (game_id, play_id).
-- Select, type, rename -- no filtering and no business logic: every play stays, so
-- a later mart can keep any slice (big plays, red-zone plays, defensive TDs) without
-- going back to raw. Of nflverse's 372 columns this keeps the ~90 the fantasy work
-- reads: situation, result, yardage, every player id on the play (offence, defence,
-- returns, kicks) and EPA / WPA. The rest stays queryable in raw_nflverse.pbp.

with source as (

    select * from {{ source('raw_nflverse', 'pbp') }}

),

renamed as (

    select
        -- Identifiers ----------------------------------------------------------
        game_id,
        cast(play_id as integer)                        as play_id,
        season,
        week,
        season_type,
        home_team                                       as home_team_abbr,
        away_team                                       as away_team_abbr,
        posteam                                         as offense_team,
        defteam                                         as defense_team,

        -- Situation ------------------------------------------------------------
        cast(qtr as integer)                            as quarter,
        quarter_seconds_remaining,
        game_seconds_remaining,
        cast(down as integer)                           as down,
        ydstogo                                         as yards_to_go,
        yardline_100                                    as yards_to_endzone,
        -- from the offence's side, before the play
        posteam_score                                   as offense_score,
        defteam_score                                   as defense_score,
        score_differential                              as offense_lead,

        -- The play -------------------------------------------------------------
        play_type,
        "desc"                                          as play_description,
        cast(coalesce(play_deleted, 0) as boolean)      as is_deleted,
        cast(coalesce(aborted_play, 0) as boolean)      as is_aborted,
        cast(coalesce(penalty, 0) as boolean)           as had_penalty,
        yards_gained,
        air_yards,
        yards_after_catch,
        passing_yards,
        receiving_yards,
        rushing_yards,
        cast(coalesce(pass_attempt, 0) as boolean)      as is_pass_attempt,
        cast(coalesce(rush_attempt, 0) as boolean)      as is_rush_attempt,
        cast(coalesce(complete_pass, 0) as boolean)     as is_complete,
        cast(coalesce(incomplete_pass, 0) as boolean)   as is_incomplete,
        cast(coalesce(interception, 0) as boolean)      as is_interception,
        cast(coalesce(sack, 0) as boolean)              as is_sack,
        cast(coalesce(fumble_lost, 0) as boolean)       as is_fumble_lost,
        cast(coalesce(safety, 0) as boolean)            as is_safety,

        -- Scoring --------------------------------------------------------------
        cast(coalesce(touchdown, 0) as boolean)         as is_touchdown,
        cast(coalesce(pass_touchdown, 0) as boolean)    as is_pass_touchdown,
        cast(coalesce(rush_touchdown, 0) as boolean)    as is_rush_touchdown,
        cast(coalesce(return_touchdown, 0) as boolean)  as is_return_touchdown,
        td_team                                         as touchdown_team,
        td_player_id                                    as touchdown_player_id,
        td_player_name                                  as touchdown_player_name,
        two_point_conv_result,
        field_goal_result,
        extra_point_result,
        kick_distance,
        cast(coalesce(defensive_extra_point_conv, 0) as boolean) as is_defensive_xp_return,

        -- Offensive players ----------------------------------------------------
        passer_player_id,
        passer_player_name,
        receiver_player_id,
        receiver_player_name,
        rusher_player_id,
        rusher_player_name,
        kicker_player_id,
        kickoff_returner_player_id,
        punt_returner_player_id,
        return_team,

        -- Defensive players ----------------------------------------------------
        solo_tackle_1_player_id,
        solo_tackle_2_player_id,
        assist_tackle_1_player_id,
        assist_tackle_2_player_id,
        assist_tackle_3_player_id,
        assist_tackle_4_player_id,
        tackle_with_assist_1_player_id,
        tackle_with_assist_2_player_id,
        tackle_for_loss_1_player_id,
        tackle_for_loss_2_player_id,
        sack_player_id,
        half_sack_1_player_id,
        half_sack_2_player_id,
        interception_player_id,
        forced_fumble_player_1_player_id,
        forced_fumble_player_2_player_id,
        fumble_recovery_1_player_id,
        fumble_recovery_1_team,
        pass_defense_1_player_id,
        pass_defense_2_player_id,
        blocked_player_id,
        safety_player_id,

        -- Value models ---------------------------------------------------------
        epa,
        wpa,
        vegas_wpa,
        wp                                              as offense_win_prob

    from source

)

select * from renamed
