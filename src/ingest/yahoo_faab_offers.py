"""Every FAAB offer in a Yahoo league, the losing ones included, from the league's
public "FAB Offers" page.

    python -m src.ingest.yahoo_faab_offers               # every Yahoo league
    python -m src.ingest.yahoo_faab_offers --league adn

Yahoo's API returns only claims that went through (and a team's own pending ones);
the losing bids exist only on the league's web page,
football.fantasysports.yahoo.com/f1/{league id}/transactions?transactionsfilter=faab.
That page needs no sign-in while the league is publicly viewable. One page lists
every contested claim of the season: the player, the winning bid and team, and each
losing offer with Yahoo's reason -- "Lower Offer", "Lower waiver priority" (a tie
lost on priority) or "Invalid claim due to previous transaction" (the team's own
claim, void after another of its claims went through; not a competing bid).

Writes data/processed/{league}/yahoo_claims.json in the shape of the ESPN leagues'
espn_claims.json -- one row per claim, won or lost -- so the waivers report reads
both the same way:

    {"week", "player", "pos", "team_id", "won", "status", "bid", "when"}

status: EXECUTED (won), LOST_LOWER_OFFER, LOST_LOWER_PRIORITY or FAILED_INVALID.
The week is the winning claim's week in yahoo_pickups.json (run after
yahoo_site_export.py). One GET per league, read-only.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

import requests

from src.ingest.discover import latest_league_key
from src.ingest.leagues import LEAGUES, keys

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = Path(os.environ.get("FFF_PROCESSED_DIR") or ROOT / "data" / "processed")
PAGE = "https://football.fantasysports.yahoo.com/f1/{lid}/transactions?transactionsfilter=faab"
REASONS = {"Lower Offer": "LOST_LOWER_OFFER", "Lower waiver priority": "LOST_LOWER_PRIORITY",
           "Invalid claim due to previous transaction": "FAILED_INVALID"}

ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
# a player links to his page; a defence to its team's ("Broncos", "Den - DEF")
PLAYER = re.compile(r'href="https://sports\.yahoo\.com/nfl/(?:players|teams)/([^/"]+)/?"[^>]*>([^<]+)</a>\s*'
                    r'<span class="F-position[^"]*">([^<]+)</span>')
WINNING = re.compile(r"\$(\d+) Winning Offer")
LOSER = re.compile(r'<p><a href="[^"]*/f1/\d+/(\d+)">[^<]*</a>\s*\$(\d+)\s*\(([^)]+)\)</p>')
AWARDED = re.compile(r'<td class="Ta-end">.*?/f1/\d+/(\d+)">[^<]*</a>\s*'
                     r'<span class="Block F-timestamp[^"]*">([^<]+)</span>', re.S)


def offers(page_html: str) -> list[dict]:
    """[{yahoo_id, player, pos, winner, bid, when, losers: [(team, bid, reason)]}]"""
    out = []
    for row in ROW.findall(page_html):
        p, w, a = PLAYER.search(row), WINNING.search(row), AWARDED.search(row)
        if not (p and w and a):
            continue
        out.append({
            "yahoo_id": p.group(1), "player": html.unescape(p.group(2)).strip(),
            "pos": {"DEF": "DST"}.get(p.group(3).split("-")[-1].strip(), p.group(3).split("-")[-1].strip()),
            "winner": a.group(1), "bid": int(w.group(1)), "when": a.group(2).strip(),
            "losers": [(t, int(b), r.strip()) for t, b, r in LOSER.findall(row)],
        })
    return out


def week_of(pickups: dict, team: str, player: str):
    """The week of `team`'s waiver claim of `player` (its latest, if more than one)."""
    weeks = [int(a["week"]) for a in pickups.get("teams", {}).get(team, [])
             if a["player"] == player and a["source"] == "waivers"]
    return max(weeks) if weeks else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=keys("yahoo"))
    a = ap.parse_args()
    for key in ([a.league] if a.league else keys("yahoo")):
        league_key = latest_league_key(LEAGUES[key]["yahoo_league_name"])
        lid = league_key.split(".l.")[-1]
        r = requests.get(PAGE.format(lid=lid), timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        found = offers(r.text)
        if not found and "Winning Offer" not in r.text:
            # a private league shows a sign-in page instead: say so, never guess
            raise SystemExit(f"{key}: no FAB offers on the page -- is the league still publicly viewable?")
        pickups_path = PROCESSED / key / "yahoo_pickups.json"
        pickups = json.loads(pickups_path.read_text(encoding="utf-8")) if pickups_path.exists() else {}
        claims = []
        for o in found:
            week = week_of(pickups, o["winner"], o["player"])
            base = {"week": week, "player": o["player"], "pos": o["pos"], "when": o["when"]}
            claims.append({**base, "team_id": int(o["winner"]), "won": True, "status": "EXECUTED", "bid": o["bid"]})
            for team, bid, reason in o["losers"]:
                claims.append({**base, "team_id": int(team), "won": False,
                               "status": REASONS.get(reason, "LOST_OTHER"), "bid": bid})
        out = PROCESSED / key / "yahoo_claims.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "league": key, "season": int(pickups.get("season") or 0) or None,
            "acquisition": {"type": "FAAB", "uses_budget": True, "source": "the league's FAB Offers page"},
            "claims": claims,
        }, indent=1), encoding="utf-8")
        print(f"{key}: {len(found)} contested offers, {len(claims)} claims -> {out}")


if __name__ == "__main__":
    main()
