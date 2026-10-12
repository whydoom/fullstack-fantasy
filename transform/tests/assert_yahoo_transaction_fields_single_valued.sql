-- stg_yahoo__transaction_players takes element [1] of each JSONPath match. Checked
-- here against the raw payload; paths must mirror the model's. Returns offending legs.

with transactions as (

    select landed_file, unnest(json_extract(payload, '$.data..transaction')) as tx
    from {{ source('raw_yahoo', 'transactions') }}

),

legs as (

    select landed_file, tx, unnest(json_extract(tx, '$..player')) as player
    from transactions

)

select
    landed_file,
    json_extract_string(tx, '$[*].transaction_key')       as transaction_key,
    json_extract_string(player, '$[*][*].player_key')     as player_key
from legs
where len(json_extract(tx, '$[*].transaction_key'))                          <> 1
   or len(json_extract(tx, '$[*].type'))                                     <> 1
   or len(json_extract(tx, '$[*].timestamp'))                                <> 1
   or len(json_extract(tx, '$[*].faab_bid'))                                 >  1
   or len(json_extract(player, '$[*][*].player_key'))                        <> 1
   or len(json_extract(player, '$[*][*].name.full'))                         <> 1
   or len(json_extract(player, '$[*].transaction_data..type'))               <> 1
   or len(json_extract(player, '$[*].transaction_data..source_type'))        <> 1
   or len(json_extract(player, '$[*].transaction_data..destination_type'))   <> 1
   or len(json_extract(player, '$[*].transaction_data..source_team_key'))      >  1
   or len(json_extract(player, '$[*].transaction_data..destination_team_key')) >  1
