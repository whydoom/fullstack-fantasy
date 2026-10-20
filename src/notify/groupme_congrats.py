"""Post a "congrats on the win" message to a league's GroupMe: every team that won
the latest final week on jimoody.com.

    python -m src.notify.groupme_congrats --site PATH --league adn [--week-file F]

Reads the site's published data (PATH is a checkout of whydoom/jimoody.com: its
fixtures/ snapshot is what the site shows, so the message always matches the
page), with the site's own team tags (managers.csv). Posts through a GroupMe bot:
GROUPME_BOT_ID in the environment (dev.groupme.com/bots; the bot belongs to one
group). With --week-file, writes the week it posted so the workflow can tell a
new week from one already congratulated. Prints only the week and how many
teams -- the scheduled run's logs are public.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

GROUPME = "https://api.groupme.com/v3/bots/post"
LIMIT = 1000          # GroupMe's cap on one message


def winners(site: Path, league: str):
    """(week, [(winner tag, team name, pts, loser tag, opp pts)]) for the latest
    final week, through the site's own loader (fixtures, managers.csv tags)."""
    os.environ["FFF_DATA"] = "fixtures"
    sys.path.insert(0, str(site))
    from leagues_site import load      # the site's module, from its checkout
    luck = load(league, "luck")
    teams = load(league, "team_weeks")["teams"]
    week = max(int(w) for w in luck["weeks"])
    side = {str(r["team_id"]): next((x for x in r.get("weekly", []) if int(x["week"]) == week), None)
            for r in luck["rows"]}
    out = []
    for tid, x in side.items():
        if not x or x["result"] != "W":
            continue
        opp = next((t for t, y in side.items() if t != tid and y and abs(y["pts"] - x["opp"]) < 0.01
                    and abs(y["opp"] - x["pts"]) < 0.01), None)
        out.append((teams[tid]["abbrev"], teams[tid]["name"], x["pts"],
                    teams[opp]["abbrev"] if opp else "?", x["opp"]))
    return week, sorted(out, key=lambda r: -(r[2] - r[4]))


def message(week, wins):
    lines = [f"Week {week} is in the books. Congrats to this week's winners:"]
    lines += [f"- {w} ({name}) {pts:.1f} to {opp_pts:.1f} over {opp}" for w, name, pts, opp, opp_pts in wins]
    lines.append("Every game, every bullet: jimoody.com")
    text = "\n".join(lines)
    return text if len(text) <= LIMIT else text[:LIMIT - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=Path, required=True)
    ap.add_argument("--league", required=True)
    ap.add_argument("--week-file", type=Path, help="write the week posted here")
    ap.add_argument("--latest-week", action="store_true", help="only print the latest final week")
    a = ap.parse_args()

    week, wins = winners(a.site, a.league)
    if a.latest_week:
        print(week)
        return
    bot = os.environ.get("GROUPME_BOT_ID", "").strip()
    if not bot:
        raise SystemExit("GROUPME_BOT_ID must be set.")
    body = json.dumps({"bot_id": bot, "text": message(week, wins)}).encode("utf-8")
    req = urllib.request.Request(GROUPME, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 201, 202):
            raise SystemExit(f"GroupMe answered {r.status}")
    if a.week_file:
        a.week_file.write_text(str(week), encoding="utf-8")
    print(f"posted week {week}: {len(wins)} winners", file=sys.stderr)


if __name__ == "__main__":
    main()
