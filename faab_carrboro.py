"""Carrboro FAAB: real roster transactions, separated from lineup noise, bucketed by cost.

    python faab_carrboro.py          # writes data/processed/carrboro/espn_faab.json
    python faab_carrboro.py 4        # ...and prints week 4's moves in detail

One FAAB script per league that uses FAAB (this one and faab_adn.py), because the
two platforms' transaction logs share nothing below the output shape: ESPN's must
be classified item by item, as below; Yahoo's is modelled in dbt.

The `type` field on an ESPN transaction is not a useful classifier. `ROSTER`
covers 202 rows in Carrboro and lumps together a free-agent pickup and a
bench-to-flex swap, which is why every earlier pass over this data was drowning
in rows that are not transactions in any sense a manager would recognise.

The items are where the truth is. Each item records where a player came from
and where he went, as `fromTeamId` / `toTeamId`, and team 0 is the free-agent
pool:

    ADD,  fromTeamId 0, toTeamId T   -> came in from the pool. An ACQUISITION.
    DROP, fromTeamId T, toTeamId 0   -> released to the pool.
    ADD/DROP between two real teams  -> a TRADE.
    from == to, both non-zero        -> he never left the roster. A LINEUP move.

So classification happens per item, and the transaction is labelled by what its
items actually did. That rule generalises: it will keep working if ESPN renames
a type or adds one, because it reads the movement rather than the label.

Acquisitions then split on bidAmount, which is the distinction that matters for
this league:

    bid >= 1  -> FAAB waiver claim, cost real budget
    bid == 0  -> free pickup off the open pool, cost nothing

Writes a JSON file alongside the report so downstream work does not have to
re-derive any of this.

Caveat worth carrying forward: for seven teams, ESPN's own
`acquisitionBudgetSpent` disagrees with the summed bids by $1-$4. The per-period
counts agree for six of those seven, so no acquisition is missing -- only the
amount charged is in question. This script reports both numbers side by side
rather than picking one.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

from src.ingest.espn import BASE, PROJECT_ROOT, EspnClient, current_season

SEASON = current_season()
LEAGUE = "carrboro"
# optional: a week number to print a detailed log for
WEEK = int(sys.argv[1]) if len(sys.argv) > 1 else None

# Per-league folder and {platform}_{dataset} name, the layout the site's renderers
# resolve through leagues_site.data_path(league, "faab").
OUT = PROJECT_ROOT / "data" / "processed" / LEAGUE / "espn_faab.json"

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
POOL = 0  # ESPN's team id for the free-agent pool


def when(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%b %d %H:%M") if ms else "?"


def collect(client):
    seen = {}
    for wk in range(0, 19):
        try:
            data = client.get(
                f"seasons/{SEASON}/segments/0/leagues/{client.league_id}",
                params=[("view", "mTransactions2"), ("scoringPeriodId", wk)],
            )
        except Exception:
            continue
        obj = data[0] if isinstance(data, list) and data else data
        for t in obj.get("transactions") or []:
            seen.setdefault(t.get("id"), t)
    return seen


def classify(t):
    """What did this transaction actually DO? Read the items, not the type."""
    if t.get("type") == "DRAFT":
        return "DRAFT"
    if str(t.get("type") or "").startswith("TRADE"):
        return "TRADE"

    items = t.get("items") or []
    pool_add = any(i.get("type") == "ADD" and (i.get("fromTeamId") or 0) == POOL
                   for i in items)
    pool_drop = any(i.get("type") == "DROP" and (i.get("toTeamId") or 0) == POOL
                    for i in items)
    # a move whose player started and ended on the same real team
    internal = any((i.get("fromTeamId") or 0) == (i.get("toTeamId") or 0) != POOL
                   for i in items)

    if pool_add:
        return "ACQUISITION"
    if pool_drop:
        return "DROP"
    if internal or any(i.get("type") == "LINEUP" for i in items):
        return "LINEUP"
    return "OTHER"


def player_names(client, ids):
    out = {}
    ids = sorted({i for i in ids if i})
    for i in range(0, len(ids), 250):
        chunk = ids[i:i + 250]
        headers = dict(client.session.headers)
        headers["X-Fantasy-Filter"] = json.dumps({"players": {
            "filterIds": {"value": chunk},
            "limit": len(chunk) + 25,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }})
        try:
            r = client.session.get(
                f"{BASE}/seasons/{SEASON}/segments/0/leagues/{client.league_id}",
                params=[("view", "kona_player_info")],
                cookies=client.cookies, headers=headers, timeout=90,
            )
            r.raise_for_status()
            d = r.json()
            obj = d[0] if isinstance(d, list) and d else d
            for e in obj.get("players") or []:
                p = e.get("player") or {}
                out[p.get("id")] = {
                    "name": p.get("fullName") or "?",
                    "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
                }
        except Exception as exc:
            print(f"  (name chunk failed: {type(exc).__name__} {str(exc)[:80]})")
    return out


def main():
    client = EspnClient(LEAGUE)
    base = client.get(
        f"seasons/{SEASON}/segments/0/leagues/{client.league_id}",
        params=[("view", "mTeam"), ("view", "mSettings")],
    )
    base = base[0] if isinstance(base, list) and base else base

    acq = ((base.get("settings") or {}).get("acquisitionSettings") or {})
    budget = acq.get("acquisitionBudget")
    teams, counter = {}, {}
    for t in base.get("teams") or []:
        teams[t["id"]] = t.get("abbrev") or f"T{t['id']}"
        counter[t["id"]] = t.get("transactionCounter") or {}

    raw = collect(client)
    buckets = Counter()
    for t in raw.values():
        buckets[classify(t)] += 1

    print(f"{LEAGUE}, season {SEASON}")
    print(f"budget ${budget}, minimum bid ${acq.get('minimumBid')}, "
          f"{acq.get('acquisitionType')}\n")
    print(f"{len(raw)} raw rows ->")
    for k in ("ACQUISITION", "DROP", "TRADE", "DRAFT", "LINEUP", "OTHER"):
        print(f"  {k:<12}{buckets[k]:>5}")
    print(f"\n  of those, LINEUP+OTHER = {buckets['LINEUP'] + buckets['OTHER']} "
          f"rows that are not transactions at all")

    # ------------------------------------------------ the acquisitions
    acqs = [t for t in raw.values()
            if classify(t) == "ACQUISITION" and t.get("status") == "EXECUTED"]
    paid = [t for t in acqs if (t.get("bidAmount") or 0) >= 1]

    # Losing bids: a claim that lost to a higher bid on the same player comes back
    # as FAILED_INVALIDPLAYERSOURCE (the player was no longer in the pool when it
    # was processed), with its bid. Matched to the winning claim by player and
    # scoring period -- every losing bid this season matches one winning claim,
    # and none exceeds the bid that beat it (checked below). PENDING claims are
    # other managers' live, unprocessed bids and are never read.
    def add_pid(t):
        return next((i.get("playerId") for i in t.get("items") or []
                     if i.get("type") == "ADD"), None)
    losing = defaultdict(list)
    for t in raw.values():
        if t.get("type") == "WAIVER" and t.get("status") == "FAILED_INVALIDPLAYERSOURCE":
            losing[(add_pid(t), t.get("scoringPeriodId"))].append(t.get("bidAmount") or 0)

    def second_bid(t):
        bids = losing.get((add_pid(t), t.get("scoringPeriodId")), [])
        if bids and max(bids) > (t.get("bidAmount") or 0):
            raise SystemExit(f"losing bid above the winning bid for player {add_pid(t)}")
        return max(bids) if bids else None
    free = [t for t in acqs if (t.get("bidAmount") or 0) == 0]

    print(f"\n{'=' * 74}")
    print("EXECUTED ACQUISITIONS, bucketed by cost")
    print("=" * 74)
    print(f"  FAAB claims (bid >= $1): {len(paid):>4}   "
          f"${sum(t['bidAmount'] for t in paid)} total")
    print(f"  free pickups (bid $0):   {len(free):>4}")
    print(f"  all acquisitions:        {len(acqs):>4}")

    names = player_names(client, {i.get("playerId") for t in acqs
                                  for i in (t.get("items") or [])})

    def added(t):
        for i in t.get("items") or []:
            if i.get("type") == "ADD" and (i.get("fromTeamId") or 0) == POOL:
                return names.get(i.get("playerId"),
                                 {"name": f"#{i.get('playerId')}", "pos": "?"})
        return {"name": "?", "pos": "?"}

    def dropped(t):
        for i in t.get("items") or []:
            if i.get("type") == "DROP" and (i.get("toTeamId") or 0) == POOL:
                return names.get(i.get("playerId"),
                                 {"name": f"#{i.get('playerId')}", "pos": "?"})
        return None

    # ------------------------------------------------ per-team summary
    print(f"\n{'=' * 74}")
    print("PER TEAM")
    print("=" * 74)
    print(f"  {'tm':<7}{'claims':>7}{'bid $':>7}{'free':>6}{'ESPN $':>8}"
          f"{'ESPN n':>7}{'our n':>7}  notes")
    rows = {}
    for tid in sorted(teams, key=lambda x: teams[x]):
        mine = [t for t in paid if t.get("teamId") == tid]
        mine_free = [t for t in free if t.get("teamId") == tid]
        bid_total = sum(t["bidAmount"] for t in mine)
        espn_dollars = counter[tid].get("acquisitionBudgetSpent") or 0
        espn_n = counter[tid].get("acquisitions")
        our_n = len(mine) + len(mine_free)
        notes = []
        if abs(bid_total - espn_dollars) > 0.01:
            notes.append(f"$ off by {bid_total - espn_dollars:+.0f}")
        if espn_n is not None and espn_n != our_n:
            notes.append(f"count off by {our_n - espn_n:+d}")
        print(f"  {teams[tid]:<7}{len(mine):>7}{bid_total:>7.0f}"
              f"{len(mine_free):>6}{espn_dollars:>8}{str(espn_n):>7}{our_n:>7}"
              f"  {', '.join(notes)}")
        rows[teams[tid]] = {
            "team_id": tid,
            "claims": [{
                "week": t.get("scoringPeriodId"),
                "bid": t.get("bidAmount"),
                "player": added(t)["name"],
                "pos": added(t)["pos"],
                "dropped": (dropped(t) or {}).get("name"),
                "when": when(t.get("processDate")),
                # next-highest bid for the same player, None if nobody else bid;
                # overspend = what the winner paid above it (the whole bid if alone)
                "second_bid": second_bid(t),
                "overspend": (t.get("bidAmount") or 0) - (second_bid(t) or 0),
            # Chronological, by when the claim processed. Sorting by bid size
            # within a week scrambles add/drop chains into nonsense: TMWA's
            # week 4 reads as dropping Kaleb Johnson twice with one add,
            # because the re-add sorted above the drop that preceded it.
            } for t in sorted(mine, key=lambda x: (x.get("processDate") or 0))],
            "free_pickups": len(mine_free),
            "bid_total": bid_total,
            "espn_budget_spent": espn_dollars,
            "espn_acquisitions": espn_n,
        }

    tot_bid = sum(r["bid_total"] for r in rows.values())
    tot_espn = sum(r["espn_budget_spent"] for r in rows.values())
    print(f"\n  league total: bids ${tot_bid:.0f}, ESPN counters ${tot_espn} "
          f"({tot_bid - tot_espn:+.0f})")

    # ------------------------------------------------ claim log
    print(f"\n{'=' * 74}")
    print("EVERY FAAB CLAIM, by team")
    print("=" * 74)
    for ab in sorted(rows):
        cl = rows[ab]["claims"]
        if not cl:
            continue
        print(f"\n  {ab}  --  ${rows[ab]['bid_total']:.0f} across "
              f"{len(cl)} claim(s); ESPN says ${rows[ab]['espn_budget_spent']}")
        for c in cl:
            drop = f"  (dropped {c['dropped']})" if c["dropped"] else ""
            print(f"    wk{str(c['week']):<3} ${str(c['bid']):<4} "
                  f"{c['player']} ({c['pos']}){drop}")

    # ------------------------------------------------ optional week log
    if WEEK is not None:
        print(f"\n{'=' * 74}")
        print(f"WEEK {WEEK} IN DETAIL -- acquisitions, drops and trades only")
        print("=" * 74)
        wk_rows = [t for t in raw.values()
                   if (t.get("scoringPeriodId") or 0) == WEEK
                   and classify(t) in ("ACQUISITION", "DROP", "TRADE")]
        print(f"  {len(wk_rows)} rows "
              f"(lineup moves excluded)\n")
        for t in sorted(wk_rows, key=lambda x: x.get("processDate") or 0):
            kind = classify(t)
            bid = t.get("bidAmount") or 0
            tag = f"${bid}" if bid else "free"
            a = added(t) if kind == "ACQUISITION" else None
            d = dropped(t)
            desc = f"+ {a['name']} ({a['pos']})" if a else ""
            if d:
                desc += f"   - {d['name']} ({d['pos']})"
            print(f"  {teams.get(t.get('teamId'), '?'):<7}{kind:<12}"
                  f"{tag:<6}{str(t.get('status')):<28}"
                  f"{when(t.get('processDate'))}")
            if desc:
                print(f"          {desc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "league": LEAGUE,
        "season": SEASON,
        "budget": budget,
        # Losing bids are published by ESPN, so second bids / overspend exist.
        "has_second_bids": True,
        "minimum_bid": acq.get("minimumBid"),
        "acquisition_type": acq.get("acquisitionType"),
        "teams": rows,
        "row_counts": dict(buckets),
    }, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
