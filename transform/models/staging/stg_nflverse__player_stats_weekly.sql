-- One row per player per game, 2020-present. Flatten, type, rename. No business logic.
--
-- Column scope: nflverse ships 150 columns here, most of them deep kicking, punting
-- and defensive splits (fg_made_40_49, pt_inside_20, def_pat_blocks, ...). This model
-- promotes the identity, context and production columns the fantasy marts actually
-- read. Everything else remains in raw_nflverse.player_stats_weekly and can be
-- promoted by adding a line below — the raw layer is not lossy, this model is
-- deliberately scoped.

with source as (

    select * from {{ source('raw_nflverse', 'player_stats_weekly') }}

),

renamed as (

    select
        -- Identity -----------------------------------------------------------
        player_id,
        player_name                             as player_short_name,
        player_display_name                     as player_full_name,
        position                                as position_code,
        position_group,

        -- Game context --------------------------------------------------------
        game_id,
        season,
        week,
        season_type,
        team                                    as team_abbr,
        opponent_team                           as opponent_team_abbr,

        -- Passing --------------------------------------------------------------
        -- `attempts` renamed: bare "attempts" is ambiguous next to carries, targets,
        -- fg_att and pat_att, all of which are also attempts of something.
        attempts                                as passing_attempts,
        completions                             as passing_completions,
        passing_yards,
        passing_tds,
        passing_interceptions,
        passing_air_yards,
        passing_first_downs,
        passing_epa,
        sacks_suffered,
        sack_yards_lost,

        -- Rushing --------------------------------------------------------------
        carries                                 as rushing_attempts,
        rushing_yards,
        rushing_tds,
        rushing_first_downs,
        rushing_fumbles_lost,
        rushing_epa,

        -- Receiving ------------------------------------------------------------
        targets,
        receptions,
        receiving_yards,
        receiving_tds,
        receiving_first_downs,
        receiving_fumbles_lost,
        receiving_air_yards,
        receiving_epa,
        target_share,
        air_yards_share,
        wopr,

        -- Kicking --------------------------------------------------------------
        fg_made                                 as field_goals_made,
        fg_att                                  as field_goals_attempted,
        fg_long                                 as field_goal_long_yards,
        pat_made                                as extra_points_made,
        pat_att                                 as extra_points_attempted,

        -- Fantasy scoring ------------------------------------------------------
        -- Precomputed by nflverse under standard scoring rules. Kept because it is a
        -- useful neutral baseline, but note it is NOT this league's scoring — league
        -- points come from the Yahoo source. Any comparison between the two belongs
        -- in a mart, not here.
        fantasy_points                          as nflverse_fantasy_points_standard,
        fantasy_points_ppr                      as nflverse_fantasy_points_ppr

    from source

    -- 131 of 112,450 rows (0.1%) carry no player identity whatsoever: null player_id,
    -- null name, null position, and zero in every single stat column — verified, they
    -- contain no data to lose. They cannot satisfy this model's declared grain of one
    -- row per player per game, and would break any join on player_id downstream, so
    -- they are excluded here and the grain is enforced by tests.
    where player_id is not null

)

select * from renamed
