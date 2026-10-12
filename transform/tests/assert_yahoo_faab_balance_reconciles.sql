-- Every team's remaining FAAB, as Yahoo reports it, must equal the budget minus the
-- bids fct_transaction attributes to that team's winning claims. This is the check
-- ESPN failed for Carrboro (seven teams off by $1-$4, unexplained); here it has to
-- hold exactly, or the page would be drawing a budget that is not the league's.
--
-- Also the evidence for the budget itself: Yahoo's settings do not state it, so
-- `faab_budget` is a var (default 100) and this test is what confirms it — a wrong
-- budget fails every team at once, including the ones that never bid.
--
-- Only meaningful when the teams snapshot and the transaction log were fetched in
-- the same run (yahoo_season refreshes both together). Returns disagreeing teams.

{% set budget = var('faab_budget', 100) %}

with bids as (

    select league_key, to_team_id as team_id, sum(faab_bid) as bids
    from {{ ref('fct_transaction') }}
    where faab_bid is not null
    group by all

)

select
    s.league_key,
    s.team_id,
    s.faab_balance,
    coalesce(b.bids, 0)                         as bids,
    {{ budget }} - coalesce(b.bids, 0)          as expected_balance
from {{ ref('stg_yahoo__team_snapshots') }} as s
left join bids as b using (league_key, team_id)
where s.faab_balance <> {{ budget }} - coalesce(b.bids, 0)
