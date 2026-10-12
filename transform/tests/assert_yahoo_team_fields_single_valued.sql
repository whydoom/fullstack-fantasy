-- stg_yahoo__teams takes element [1] of each JSONPath match, and assumes one manager
-- per team. Both are checked here against the raw payload; a co-managed team fails
-- this rather than having one manager chosen arbitrarily. Paths must mirror the
-- model's. Returns the offending rows.

with teams as (

    select
        landed_file,
        unnest(json_extract(payload, '$.data..team')) as team
    from {{ source('raw_yahoo', 'teams') }}

)

select
    landed_file,
    json_extract_string(team, '$[*][*].team_key') as team_key
from teams
where len(json_extract(team, '$[*][*].team_key'))                          <> 1
   or len(json_extract(team, '$[*][*].team_id'))                           <> 1
   or len(json_extract(team, '$[*][*].name'))                              <> 1
   or len(json_extract(team, '$[*][*].url'))                               <> 1
   or len(json_extract(team, '$[*][*].team_logos[*].team_logo.url'))       >  1
   or len(json_extract(team, '$[*][*].previous_season_team_rank'))         >  1
   or len(json_extract(team, '$[*][*].managers[*].manager'))               <> 1
