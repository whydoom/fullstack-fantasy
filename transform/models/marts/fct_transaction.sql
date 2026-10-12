-- One row per transaction + player: every player movement in the league's
-- transaction log, with the FAAB bid attributed to the leg it paid for.
--
-- The one business rule here is bid attribution. Yahoo reports faab_bid on the
-- transaction, so in an add/drop both legs carry it; summing it as-is would charge
-- every claim twice. It belongs to the ADD leg — the player the money bought —
-- and is null on drops. Free-agent adds (no waiver period) have no bid at all.
--
-- Week assignment: the first fantasy week whose window ends on or after the
-- transaction's date in US Eastern time (Yahoo's week windows are US dates; its
-- timestamps are UTC, and an 11pm Sunday ET move is a Monday in UTC). Moves made
-- before week 1 — the preseason — land in week 1, the first lineup they affect.

with legs as (

    select * from {{ ref('stg_yahoo__transaction_players') }}

),

calendar as (

    select distinct league_key, week, week_start_date, week_end_date
    from {{ ref('stg_yahoo__matchup_teams') }}

),

dated as (

    select
        legs.*,
        cast(transacted_at at time zone 'America/New_York' as date)   as transacted_on_et
    from legs

)

select
    -- Grain ---------------------------------------------------------------------
    d.league_key,
    d.transaction_id,
    d.player_key,

    -- Context -------------------------------------------------------------------
    d.transaction_key,
    d.transaction_type,
    d.leg_type,
    d.source_type,
    cast(regexp_extract(d.source_team_key, '\.t\.(\d+)$', 1) as integer)       as from_team_id,
    cast(regexp_extract(d.destination_team_key, '\.t\.(\d+)$', 1) as integer)  as to_team_id,
    d.transacted_at,
    d.transacted_on_et,
    (
        select min(c.week)
        from calendar as c
        where c.league_key = d.league_key
          and c.week_end_date >= d.transacted_on_et
    )                                                                          as week,

    -- The business rule ------------------------------------------------------------
    d.leg_type = 'add' and d.source_type = 'waivers'                           as is_waiver_claim,
    case when d.leg_type = 'add' then d.transaction_faab_bid end               as faab_bid

from dated as d
