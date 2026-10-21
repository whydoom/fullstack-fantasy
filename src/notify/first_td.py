"""The week's first touchdown, called on Thursday night.

    python -m src.notify.first_td --sent state.json --out out/adn
    python -m src.notify.first_td --week 6 --out x        # a past week

Reads ESPN's public NFL scoreboard (no sign-in): the week's games in kickoff order,
and for the first game that has a touchdown, its scoring plays in order. The first
touchdown's text ("J.K. Dobbins 4 Yd Rush (Wil Lutz Kick)", "Puka Nacua 23 Yd pass
from Matthew Stafford (...)") gives the scorer and, for a catch, the passer:

    X scored this week's first touchdown on a pass from Y.

Writes DIR/message.txt and DIR/subject.txt the first time the week has a
touchdown, and records the week in --sent so it is called once. Prints only what
it found, never a name: the logs are public. A network hiccup skips this check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

NFL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
YARDS = re.compile(r"\s+-?\d+\s+Yd\b", re.I)
PASS_FROM = re.compile(r"\bpass from\s+(.+?)\s*(?:\(|$)", re.I)


def first_touchdown(week: int | None, season: int | None):
    """(week, season, scorer, passer or None) for the week's first touchdown so far,
    or (week, season, None, None) when nobody has scored one yet."""
    params = {"seasontype": 2}
    if week:
        params.update(week=week, dates=season)
    sb = requests.get(f"{NFL}/scoreboard", params=params, timeout=30)
    sb.raise_for_status()
    sb = sb.json()
    week = int(sb["week"]["number"])
    season = int(sb.get("season", {}).get("year") or season or 0)
    for e in sorted(sb.get("events", []), key=lambda e: e["date"]):
        if e["status"]["type"]["state"] == "pre":
            continue
        s = requests.get(f"{NFL}/summary", params={"event": e["id"]}, timeout=30)
        s.raise_for_status()
        for play in s.json().get("scoringPlays", []):
            kind = (play.get("scoringType") or {}).get("abbreviation") or (play.get("type") or {}).get("abbreviation")
            if kind != "TD":
                continue
            text = play.get("text", "")
            m = YARDS.search(text)
            scorer = (text[:m.start()] if m else text.split("(")[0]).strip()
            p = PASS_FROM.search(text)
            return week, season, scorer, (p.group(1).strip() if p else None)
    return week, season, None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="default: ESPN's current week")
    ap.add_argument("--season", type=int)
    ap.add_argument("--sent", type=Path, help="JSON: the weeks already called")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--week-only", action="store_true")
    a = ap.parse_args()
    try:
        week, season, scorer, passer = first_touchdown(a.week, a.season)
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        print(f"NFL data unavailable this time ({type(e).__name__}); skipped", file=sys.stderr)
        return
    if a.week_only:
        print(f"{season}-w{week}")
        return
    a.out.mkdir(parents=True, exist_ok=True)
    for name in ("message.txt", "subject.txt"):
        (a.out / name).unlink(missing_ok=True)
    sent = json.loads(a.sent.read_text(encoding="utf-8")) if a.sent and a.sent.exists() else {}
    key = f"{season}-w{week}"
    if not scorer:
        print(f"week {week}: no touchdown yet", file=sys.stderr)
        return
    if key in sent.get("weeks", []):
        print(f"week {week}: first touchdown already called", file=sys.stderr)
        return
    text = f"{scorer} scored this week's first touchdown" + (f" on a pass from {passer}" if passer else "") + "."
    (a.out / "message.txt").write_text(text, encoding="utf-8")
    (a.out / "subject.txt").write_text(f"Week {week}: the first touchdown", encoding="utf-8")
    if a.sent:
        a.sent.parent.mkdir(parents=True, exist_ok=True)
        a.sent.write_text(json.dumps({"weeks": sorted(set(sent.get("weeks", [])) | {key})}), encoding="utf-8")
    print(f"week {week}: first touchdown found ({'a pass' if passer else 'not a pass'})", file=sys.stderr)


if __name__ == "__main__":
    main()
