-- Referential integrity for the composite (league_key, team_id) key, which dbt's
-- built-in `relationships` test cannot express (it takes one column). Every team
-- that appears in a Yahoo fact — as a roster owner, a matchup side, or an
-- opponent — must exist in dim_team_season. Returns the orphaned keys.

with fact_teams as (

    select distinct league_key, team_id, 'fct_roster_slot' as found_in
    from {{ ref('fct_roster_slot') }}

    union all

    select distinct league_key, team_id, 'fct_matchup'
    from {{ ref('fct_matchup') }}

    union all

    select distinct league_key, opponent_team_id, 'fct_matchup (opponent)'
    from {{ ref('fct_matchup') }}

)

select f.*
from fact_teams as f
left join {{ ref('dim_team_season') }} as d
    on  d.league_key = f.league_key
    and d.team_id    = f.team_id
where d.team_id is null
