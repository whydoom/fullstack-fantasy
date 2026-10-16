-- One row per player per week: ESPN's pre-game projection, as named stat
-- components. Grain: (season, week, espn_id).
--
-- ESPN's stat ids, confirmed by matching ESPN's actual stat lines against nflverse's
-- box scores (each id matched the box-score column on every player checked):
--   3 passing yards   4 passing TDs     24 rushing yards   25 rushing TDs
--   53 receptions     42 receiving yds  43 receiving TDs   58 targets
--
-- player_id is the nflverse (GSIS) id, through the crosswalk's espn_id, else the
-- player_names seed (rookies the crosswalk has not caught up on). Kickers and team
-- defences have none and keep only espn_id; they are not scored yet.

with projections as (

    select * from {{ source('raw_espn', 'player_projections') }}

),

crosswalk as (

    select cast(espn_id as bigint) as espn_id, min(gsis_id) as gsis_id
    from {{ source('raw_nflverse', 'player_id_crosswalk') }}
    where espn_id is not null and gsis_id is not null
    group by 1

),

names as (

    select cast(espn_id as bigint) as espn_id, min(gsis_id) as gsis_id
    from {{ ref('player_names') }}
    where espn_id is not null and gsis_id is not null
    group by 1

),

wide as (

    select
        season,
        week,
        espn_id,
        sum(value) filter (where stat_id = 3)   as pass_yd,
        sum(value) filter (where stat_id = 4)   as pass_td,
        sum(value) filter (where stat_id = 24)  as rush_yd,
        sum(value) filter (where stat_id = 25)  as rush_td,
        sum(value) filter (where stat_id = 53)  as reception,
        sum(value) filter (where stat_id = 42)  as rec_yd,
        sum(value) filter (where stat_id = 43)  as rec_td,
        sum(value) filter (where stat_id = 58)  as target
    from projections
    group by season, week, espn_id

)

select
    wide.*,
    coalesce(crosswalk.gsis_id, names.gsis_id)  as player_id
from wide
left join crosswalk using (espn_id)
left join names using (espn_id)
