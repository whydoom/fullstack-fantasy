-- stg_yahoo__roster_slots takes element [1] of each JSONPath match. That is only
-- correct if every path matches exactly once per player (and team_key / roster.week
-- once per payload). If Yahoo ever nests a second match — as it already does with a
-- linked_player's player_key — [1] would silently pick one. This test turns that into
-- a failure instead. Paths here must mirror the model's. Returns the offending rows.

with players as (

    select
        landed_file,
        payload,
        unnest(json_extract(payload, '$.data..player')) as player
    from {{ source('raw_yahoo', 'rosters') }}

)

select
    landed_file,
    json_extract_string(player, '$[*][*].player_key') as player_key
from players
where len(json_extract(payload, '$.data..team_key'))                     <> 1
   or len(json_extract(payload, '$.data..roster.week'))                  <> 1
   or len(json_extract(player, '$[*][*].player_key'))                    <> 1
   or len(json_extract(player, '$[*][*].player_id'))                     <> 1
   or len(json_extract(player, '$[*][*].name.full'))                     <> 1
   or len(json_extract(player, '$[*][*].editorial_team_abbr'))           <> 1
   or len(json_extract(player, '$[*][*].display_position'))              <> 1
   or len(json_extract(player, '$[*][*].eligible_positions'))            <> 1
   or len(json_extract(player, '$[*].selected_position[*].position'))    <> 1
   or len(json_extract(player, '$[*].player_points.total'))              <> 1
   or len(json_extract(player, '$[*][*].linked_player.player_key'))      >  1
