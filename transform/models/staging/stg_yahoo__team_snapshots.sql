-- One row per league + team: the running counters Yahoo reports on each team, as of
-- the fetch. Deliberately separate from stg_yahoo__teams (and dim_team_season):
-- these change week to week and only mean anything alongside fetched_at, so they
-- are snapshot measures, not team attributes. The FAAB reconciliation test checks
-- faab_balance against the transaction log.

with teams as (

    select
        payload,
        unnest(json_extract(payload, '$.data..team')) as team
    from {{ source('raw_yahoo', 'teams') }}

)

select
    regexp_extract(json_extract_string(team, '$[*][*].team_key')[1],
                   '^(.*)\.t\.\d+$', 1)                                       as league_key,
    cast(json_extract_string(team, '$[*][*].team_id')[1] as integer)          as team_id,
    cast(json_extract_string(team, '$[*][*].faab_balance')[1] as integer)     as faab_balance,
    cast(json_extract_string(team, '$[*][*].waiver_priority')[1] as integer)  as waiver_priority,
    cast(json_extract_string(team, '$[*][*].number_of_moves')[1] as integer)  as number_of_moves,
    cast(payload ->> '$._meta.fetched_at' as timestamptz)                     as snapshot_at
from teams
