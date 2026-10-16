-- One row per player per weekly injury report: what is hurt, how he practised,
-- and his game status. A week's report comes before that week's game. Grain:
-- (season, week, team_abbr, player_id). Rename only.

select
    season,
    week,
    game_type                   as season_type,
    team                        as team_abbr,
    gsis_id                     as player_id,
    full_name                   as player_name,
    position,
    report_primary_injury,
    report_secondary_injury,
    report_status,
    practice_primary_injury,
    practice_status

from {{ source('raw_nflverse', 'injuries') }}
where gsis_id is not null
