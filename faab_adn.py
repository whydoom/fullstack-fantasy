"""ADN FAAB: winning waiver bids per team, for the site's FAAB chart.

    python faab_adn.py               # writes data/processed/adn/yahoo_faab.json

Reads the dbt marts, so run the Yahoo landing, load and `dbt build` first. Every
number here is already modelled and tested: fct_transaction attributes each bid to
the add leg it paid for, and tests/assert_yahoo_faab_balance_reconciles.sql proves
every team's Yahoo balance equals the budget minus those bids. This script only
reshapes them into the JSON faab_carrboro.py writes, so jimoody.com's
render_faab.py draws both leagues with the same code.

One FAAB script per league that uses FAAB (this one and faab_carrboro.py).
"""

from __future__ import annotations

from collections import defaultdict
import json

import duckdb

from src.ingest.leagues import LEAGUES
from yahoo_site_export import (_ET, PROCESSED, WAREHOUSE_PATH, abbreviations, label,
                               position_group, resolve_league_key, rows)

LEAGUE = "adn"


def export_faab(con, lk, season, abbrev, out_dir, budget: int = 100) -> None:
    """Winning waiver bids per team, in espn_transactions.py's shape.

    budget matches the dbt var faab_budget; tests/assert_yahoo_faab_balance_reconciles
    proves it against Yahoo's reported balances, so it is not just assumed.
    Claims are in processing order, not bid order: sorting by bid scrambles
    add/drop chains (the lesson from the Carrboro chart).
    """
    claims = defaultdict(list)
    for r in rows(con, """
        select a.to_team_id as team_id, a.week, a.faab_bid, a.transacted_at,
               p.player_name, p.display_position,
               d.player_name as dropped
        from fct_transaction a
        join stg_yahoo__transaction_players p using (transaction_key, player_key)
        left join fct_transaction d_f
            on  d_f.transaction_key = a.transaction_key and d_f.leg_type = 'drop'
        left join stg_yahoo__transaction_players d
            on  d.transaction_key = d_f.transaction_key and d.player_key = d_f.player_key
        where a.league_key = ? and a.is_waiver_claim
        order by a.transacted_at, a.transaction_id""", lk):
        claims[r["team_id"]].append({
            "week": r["week"], "bid": r["faab_bid"], "player": r["player_name"],
            "pos": label(position_group(r["display_position"])),
            "dropped": r["dropped"],
            "when": r["transacted_at"].astimezone(_ET).strftime("%b %d %H:%M"),
        })
    free = {r["team_id"]: r["n"] for r in rows(con, """
        select to_team_id as team_id, count(*) as n from fct_transaction
        where league_key = ? and leg_type = 'add' and source_type = 'freeagents'
        group by 1""", lk)}
    balance = {r["team_id"]: r["faab_balance"] for r in rows(con, """
        select team_id, faab_balance from stg_yahoo__team_snapshots
        where league_key = ?""", lk)}
    teams = {}
    for tid in sorted(abbrev):
        cl = claims.get(tid, [])
        teams[abbrev[tid]] = {
            "team_id": tid, "free_pickups": free.get(tid, 0),
            "bid_total": sum(c["bid"] for c in cl),
            # What Yahoo itself says was spent; the renderer flags any team where
            # this and the summed claims disagree (none do, per the dbt test).
            "counter_spent": budget - balance[tid],
            "claims": cl,
        }
    (out_dir / "yahoo_faab.json").write_text(json.dumps({
        "league": LEAGUE, "season": season, "budget": budget,
        "counter_name": "Yahoo's remaining-balance figure",
        "teams": teams}, indent=2), encoding="utf-8")
    n = sum(len(t["claims"]) for t in teams.values())
    print(f"  wrote yahoo_faab.json ({n} winning claims, "
          f"${sum(t['bid_total'] for t in teams.values())} committed)")


def main() -> None:
    lk = resolve_league_key(LEAGUES[LEAGUE]["yahoo_league_name"])
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        teams = rows(con, "select team_id, manager_nickname, season from dim_team_season "
                          "where league_key = ?", lk)
        if not teams:
            raise SystemExit(f"No teams for {lk} in the warehouse. Load and build first.")
        abbrev = abbreviations({t["team_id"]: t["manager_nickname"] for t in teams})
        out_dir = PROCESSED / LEAGUE
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"{LEAGUE} ({lk})")
        export_faab(con, lk, teams[0]["season"], abbrev, out_dir)
    finally:
        con.close()


if __name__ == "__main__":
    main()
