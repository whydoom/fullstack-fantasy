-- One row per player per team per week: who was on the roster, with his jersey
-- number. Grain: (season, week, team_abbr, player_id). Rename only.

select
    season,
    week,
    game_type                   as season_type,
    team                        as team_abbr,
    jersey_number,
    gsis_id                     as player_id,
    full_name                   as player_name,
    position,
    status

from {{ source('raw_nflverse', 'rosters_weekly') }}
where gsis_id is not null
