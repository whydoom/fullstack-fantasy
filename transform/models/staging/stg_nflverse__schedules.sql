-- One row per NFL game, 2020-present. Flatten, type, rename. No business logic.
--
-- The real work here is typing: nflverse ships gameday, gametime, overtime and
-- div_game as strings and 0/1 integers, and every downstream model would otherwise
-- re-guess them.

with source as (

    select * from {{ source('raw_nflverse', 'schedules') }}

),

renamed as (

    select
        -- Identifiers -------------------------------------------------------
        game_id,
        old_game_id,
        season,
        week,
        game_type,

        -- When --------------------------------------------------------------
        -- gameday and gametime arrive as 'YYYY-MM-DD' and 'HH:MM' strings.
        -- Deliberately kept as separate date and time rather than combined into a
        -- timestamp: gametime is US Eastern, and nflverse does not carry a timezone
        -- with it. Stamping one on here would be an assumption dressed up as a type.
        cast(gameday as date)                   as game_date,
        cast(gametime as time)                  as kickoff_time_et,
        weekday                                 as game_weekday,

        -- Teams and result ---------------------------------------------------
        home_team                               as home_team_abbr,
        away_team                               as away_team_abbr,
        home_score,
        away_score,
        -- nflverse's `result` is home_score - away_score; renamed because "result"
        -- reads like a win/loss label rather than a margin.
        result                                  as home_score_margin,
        total                                   as combined_score,
        cast(overtime as boolean)               as went_to_overtime,
        cast(div_game as boolean)               as is_divisional_game,

        -- Rest days going into the game --------------------------------------
        home_rest                               as home_rest_days,
        away_rest                               as away_rest_days,

        -- Closing betting market ---------------------------------------------
        spread_line,
        total_line,
        home_moneyline,
        away_moneyline,

        -- Venue and conditions -----------------------------------------------
        stadium_id,
        stadium                                 as stadium_name,
        location                                as venue_type,
        roof                                    as roof_type,
        surface                                 as surface_type,
        temp                                    as temperature_f,
        wind                                    as wind_mph,

        -- People ---------------------------------------------------------------
        home_qb_id,
        home_qb_name,
        away_qb_id,
        away_qb_name,
        home_coach,
        away_coach,
        referee                                 as referee_name

    from source

)

select * from renamed
