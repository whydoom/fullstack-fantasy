-- stg_yahoo__matchup_teams takes element [1] of each JSONPath match, and derives the
-- opponent by assuming every matchup holds exactly two teams. Both assumptions are
-- checked here against the raw payload. Paths must mirror the model's. Returns the
-- offending rows.

with matchups as (

    select
        landed_file,
        unnest(json_extract(payload, '$.data..matchup')) as matchup
    from {{ source('raw_yahoo', 'scoreboards') }}

),

sides as (

    select
        landed_file,
        matchup,
        unnest(json_extract(matchup, '$..team')) as team
    from matchups

)

select
    landed_file,
    matchup ->> '$.week'                              as week,
    json_extract_string(team, '$[*][*].team_key')     as team_key
from sides
where len(json_extract(matchup, '$..team[*][*].team_key'))         <> 2
   or len(json_extract(team, '$[*][*].team_key'))                  <> 1
   or len(json_extract(team, '$[*].team_points.total'))            <> 1
   or len(json_extract(team, '$[*].team_projected_points.total'))  <> 1
