-- One row per team as it existed in a single league-season: the conformed "who"
-- for every Yahoo fact, joined on (league_key, team_id).
--
-- Type 1: attributes are the latest fetched. A team renamed in week 6 shows its
-- week-6 name against week-1 facts too. That matches how the site presents a team
-- (by its current name), and a season-scoped team is short-lived enough that name
-- history is not worth an SCD-2. Revisit if a page ever needs "what was this team
-- called that week".
--
-- No abbreviation column: Yahoo does not provide one. The short labels on the site
-- (e.g. ALCN) are a presentation choice and are made in the render step.

select
    -- Grain ---------------------------------------------------------------------
    league_key,
    team_id,

    -- Attributes ----------------------------------------------------------------
    season,
    team_key,
    team_name,
    manager_id,
    manager_nickname,
    is_commissioner,
    previous_season_rank,
    team_logo_url,
    team_url,
    fetched_at                          as attributes_as_of

from {{ ref('stg_yahoo__teams') }}
