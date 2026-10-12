-- One row per league + period + position group + player: points scored and the
-- positional finish ("RB7") under this league's scoring. Feeds the leaderboards and
-- the rank shown in every roster-grid cell.
--
-- Rank is computed here, not taken from Yahoo's page order: the pages are fetched
-- separately, so their concatenated order is only as stable as Yahoo's tie-breaking.
-- row_number over points, then name, gives a deterministic rank (ties broken
-- alphabetically, the same rule the ESPN pipeline uses), so ESPN and Yahoo ranks
-- mean the same thing on the site.
--
-- Ranks are exact within the landed depth (top 75 per position) because Yahoo sorts
-- the whole pool before paging. Beyond it a player simply has no rank.

select
    -- Grain -------------------------------------------------------------------
    league_key,
    period_key,
    position_group,
    player_key,

    -- Context -----------------------------------------------------------------
    season,
    week,
    yahoo_player_id,
    player_name,

    -- Measures ----------------------------------------------------------------
    fantasy_points,
    row_number() over (
        partition by league_key, period_key, position_group
        order by fantasy_points desc, player_name
    )                                                       as position_rank

from {{ ref('stg_yahoo__player_pool') }}
