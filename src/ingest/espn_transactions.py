"""Reading an ESPN league's transaction log: shared by faab_carrboro.py (FAAB
budgets and second bids) and pickups_espn.py (every player added, any league).

The `type` field on an ESPN transaction is not a useful classifier -- ROSTER
lumps a free-agent pickup together with a bench-to-flex swap. The items are
where the truth is: each records where a player came from and went, as
fromTeamId / toTeamId, and team 0 is the free-agent pool:

    ADD,  fromTeamId 0, toTeamId T   -> came in from the pool. An ACQUISITION.
    DROP, fromTeamId T, toTeamId 0   -> released to the pool.
    ADD/DROP between two real teams  -> a TRADE.
    from == to, both non-zero        -> he never left the roster. A LINEUP move.

GET requests only.
"""

from __future__ import annotations

import json
from datetime import datetime

from src.ingest.espn import BASE

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
POOL = 0  # ESPN's team id for the free-agent pool


def when(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%b %d %H:%M") if ms else "?"


def collect(client, season):
    """Every transaction this season, de-duplicated by id. ESPN serves the log
    per scoring period, so it is swept period by period."""
    seen = {}
    for wk in range(0, 19):
        try:
            data = client.get(
                f"seasons/{season}/segments/0/leagues/{client.league_id}",
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


def added_id(t):
    return next((i.get("playerId") for i in t.get("items") or []
                 if i.get("type") == "ADD" and (i.get("fromTeamId") or 0) == POOL), None)


def dropped_id(t):
    return next((i.get("playerId") for i in t.get("items") or []
                 if i.get("type") == "DROP" and (i.get("toTeamId") or 0) == POOL), None)


def player_names(client, season, ids):
    """{player id: {"name", "pos"}} for the given ids, 250 per request."""
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
                f"{BASE}/seasons/{season}/segments/0/leagues/{client.league_id}",
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
