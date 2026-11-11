"""A completed trade, posted to the league's GroupMe: "Trade! COBE gets ...; BRO gets ...".

    python -m src.notify.trade_alert --site site --league adn --seen .trades/seen.json --out out

Yahoo leagues only (Yahoo's league transactions, type trade, status successful).
Proposals are private to the two managers -- Yahoo answers 400 for any team but the
token's own -- so only trades that went through are posted.

Started by jimoody.com's Worker at 7am, noon, 5pm and 10pm Eastern. Each run posts the
trades it has not posted before (--seen, a list of transaction keys kept in the
workflow's cache) and only trades from the last 36 hours, so a lost cache never posts
an old trade again. Writes out/message.txt when there is something to send; team tags
come from jimoody.com's managers.csv through the site's loader, so they match the site.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from src.ingest.discover import latest_league_key
from src.ingest.leagues import LEAGUES
from src.notify.live_final import entries, find, flat

FRESH = 36 * 3600          # seconds: older trades are never posted


def trades(client, league_key: str) -> list[dict]:
    """[{"key", "when", "moves": [(player, pos, from team id, to team id)]}], completed only."""
    d = client.get(f"league/{league_key}/transactions;types=trade;count=25")
    out = []
    for tx in entries(find(d, "transactions") or {}, "transaction"):
        head = flat(tx[0] if isinstance(tx, list) else tx)
        if head.get("type") != "trade" or head.get("status") != "successful":
            continue
        moves = []
        for pl in entries(find(tx, "players") or {}, "player"):
            info, data = flat(pl[0]), flat(pl[1].get("transaction_data") if isinstance(pl[1], dict) else pl[1])
            moves.append((info["name"]["full"], (info.get("display_position") or "").split(",")[0],
                          data["source_team_key"].split(".t.")[-1], data["destination_team_key"].split(".t.")[-1]))
        out.append({"key": head["transaction_key"], "when": int(head.get("timestamp") or 0), "moves": moves})
    return out


def message(trade: dict, tag) -> str:
    gets = {}
    for name, pos, _src, dst in trade["moves"]:
        gets.setdefault(dst, []).append(f"{name} ({pos})" if pos else name)
    sides = "; ".join(f"{tag(t)} gets {', '.join(ps)}" for t, ps in gets.items())
    return f"\U0001F504 Trade! {sides}."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=Path, required=True, help="a jimoody.com checkout (team tags)")
    ap.add_argument("--league", required=True)
    ap.add_argument("--seen", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if LEAGUES[a.league]["platform"] != "yahoo":
        raise SystemExit(f"{a.league}: trade alerts are Yahoo only")

    os.environ.setdefault("FFF_PIPELINE_DIR", str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(a.site.resolve()))
    from leagues_site import team_tags                  # the site's tags (managers.csv)
    from src.ingest.yahoo_client import YahooFantasyClient

    tags = team_tags(a.league)
    seen = set(json.loads(a.seen.read_text(encoding="utf-8"))) if a.seen.exists() else set()
    found = trades(YahooFantasyClient(), latest_league_key(LEAGUES[a.league]["yahoo_league_name"]))
    now = time.time()
    new = [t for t in sorted(found, key=lambda t: t["when"])
           if t["key"] not in seen and now - t["when"] <= FRESH]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "message.txt").unlink(missing_ok=True)
    if new:
        text = "\n\n".join(message(t, lambda tid: tags.get(tid, f"Team {tid}")) for t in new)
        (a.out / "message.txt").write_text(text, encoding="utf-8")
    a.seen.parent.mkdir(parents=True, exist_ok=True)
    a.seen.write_text(json.dumps(sorted(seen | {t["key"] for t in found})), encoding="utf-8")
    print(f"{a.league}: {len(found)} completed trades, {len(new)} new to post")


if __name__ == "__main__":
    main()
