-- One row per NFL player. Grain: player_id (nflverse's GSIS id).
--
-- Not Yahoo's player_key, despite the original scaffold in _marts__schema.yml
-- assuming that grain — that was written before any real payload existed, for either
-- source. nflverse is what actually landed first, so this model's natural key is
-- nflverse's player_id today. stg_nflverse__player_id_crosswalk (joined below) adds
-- a nullable yahoo_id specifically so that once Yahoo data lands, the two identity
-- systems can be reconciled through this table rather than by re-deriving a mapping
-- from scratch or matching on name.
--
-- Attributes here are "most recently known" (a type-1 snapshot: the latest game each
-- player appeared in, no history kept) — appropriate for a dimension used to look a
-- player up, e.g. "what position does Player X play." It is deliberately NOT what
-- position/team they were in any specific week: 1,699 of 4,061 players (42%) changed
-- position or team at least once across 2020-2025, so a fact about "week 3, 2022"
-- needs that week's actual team, not today's. That's why team_abbr and
-- position_code are also carried directly on fct_player_week as degenerate
-- dimensions, not just looked up here — this dimension answers "who is this player,"
-- the fact answers "what were they for this specific game."

with player_stats as (

    select * from {{ ref('stg_nflverse__player_stats_weekly') }}

),

-- One row per player's single most recent game appearance. Safe without a tiebreaker
-- clause: verified no player has two rows sharing the same (season, week) — REG and
-- POST week numbers never overlap (REG maxes at 17-18, POST continues at 21-22), so
-- there is no season_type collision to break a tie on.
most_recent_appearance as (

    select
        player_id,
        player_full_name,
        player_short_name,
        position_code,
        position_group,
        team_abbr,
        season,
        week,
        row_number() over (
            partition by player_id
            order by season desc, week desc
        ) as recency_rank
    from player_stats

),

crosswalk as (

    select * from {{ ref('stg_nflverse__player_id_crosswalk') }}

)

select
    p.player_id,
    p.player_full_name,
    p.player_short_name,
    p.position_code                             as most_recent_position_code,
    p.position_group                            as most_recent_position_group,
    p.team_abbr                                 as most_recent_team_abbr,
    p.season                                    as most_recent_season,
    p.week                                      as most_recent_week,
    -- Nullable: 612 of 4,061 players (15%) have no match in the crosswalk snapshot,
    -- expected because that crosswalk is fantasy-relevance-driven and skips many
    -- non-skill-position players. Absence here means "not yet mapped," not an error.
    c.yahoo_id,
    c.espn_id,
    c.sleeper_id
from most_recent_appearance p
left join crosswalk c on p.player_id = c.player_id
where p.recency_rank = 1
