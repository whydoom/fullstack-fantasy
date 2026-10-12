-- One row per league + roster position: the lineup slots a league plays and how
-- many of each, from the league settings. BN and IR are included and flagged, not
-- filtered: which slots "count" is a business rule, made downstream (the same one
-- fct_roster_slot.is_starter encodes).

with source as (

    select * from {{ source('raw_yahoo', 'settings') }}

),

positions as (

    select
        payload,
        unnest(json_extract(payload, '$.data..roster_position')) as roster_position
    from source

)

select
    cast(payload ->> '$._meta.season' as integer)                     as season,
    regexp_extract(payload ->> '$._meta.resource_path',
                   '^league/([^/]+)/', 1)                             as league_key,
    roster_position ->> '$.position'                                  as position,
    cast(roster_position ->> '$.count' as integer)                    as slot_count,
    roster_position ->> '$.position' in ('BN', 'IR')                  as is_reserve_slot

from positions
-- '$..roster_position' also matches the parent list that holds the entries; only
-- the entries themselves carry a position.
where roster_position ->> '$.position' is not null
