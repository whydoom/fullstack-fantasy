-- One row per NFL game. Grain: game_id.
--
-- The typed, renamed game record from stg_nflverse__schedules, plus when and where
-- the game sits for fantasy purposes:
--
--   slot          thursday_night / thursday_day (Thanksgiving) / sunday_early
--                 (before 4pm ET, London mornings included) / sunday_late /
--                 sunday_night / monday / other_day (Wednesday, Friday, Saturday)
--   slate         the coarse version the recap tells a week in: thursday,
--                 sunday_day, sunday_night, monday, other_day
--   region        US, Europe or International, from the stadium (seed nfl_venues;
--                 a stadium not listed there is in the US)
--   is_primetime  kickoff at 8pm ET or later, in the US
--
-- Kickoff times are US Eastern as nflverse publishes them.

with games as (

    select * from {{ ref('stg_nflverse__schedules') }}

),

venues as (

    select stadium_name, region from {{ ref('nfl_venues') }}

)

select
    games.*,
    case
        when game_weekday = 'Thursday' and kickoff_time_et >= time '20:00' then 'thursday_night'
        when game_weekday = 'Thursday'                                       then 'thursday_day'
        when game_weekday = 'Sunday'   and kickoff_time_et <  time '16:00' then 'sunday_early'
        when game_weekday = 'Sunday'   and kickoff_time_et <  time '20:00' then 'sunday_late'
        when game_weekday = 'Sunday'                                         then 'sunday_night'
        when game_weekday = 'Monday'                                         then 'monday'
        else 'other_day'
    end                                                     as slot,
    case
        when game_weekday = 'Thursday'                                       then 'thursday'
        when game_weekday = 'Sunday'   and kickoff_time_et <  time '20:00' then 'sunday_day'
        when game_weekday = 'Sunday'                                         then 'sunday_night'
        when game_weekday = 'Monday'                                         then 'monday'
        else 'other_day'
    end                                                     as slate,
    coalesce(venues.region, 'US')                           as region,
    kickoff_time_et >= time '20:00'
        and coalesce(venues.region, 'US') = 'US'            as is_primetime

from games
left join venues using (stadium_name)
