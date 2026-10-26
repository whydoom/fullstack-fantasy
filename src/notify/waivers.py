"""The Wednesday waivers report: the claims that went through overnight, for a
league's GroupMe or email.

    python -m src.notify.waivers --site ../jimoody.com --league adn --out out/adn
    python -m src.notify.waivers --site ../jimoody.com --league adn --week 6 --out x   # a past week

Reads the league's exported pickups (and claims and FAAB, where the league has
them) through jimoody.com's own loader, so team tags match the site. The week is
the one after the latest final week: Wednesday's claims are for the coming games.

The header counts the contested claims (another team claimed him too), all claims
and the dollars spent. Each claim: team, player, then the bid -- and, when contested,
how many lower bids and the 2nd-highest with whose it was -- or, under traditional
waivers, the team's place in the standings and the teams with lower claims. Then the
players dropped, in one paragraph.

Writes DIR/waivers.txt (one GroupMe message, checked under its 1,000-character cap),
waivers.html and waivers_subject.txt -- or DIR/none when no waiver claim went
through that week, so nothing is sent. Prints only counts: the logs are public.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

LIMIT = 1000          # GroupMe's cap on one message
BOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                     "".join(chr(0x1D400 + i) for i in range(52)))   # GroupMe has no markup


# a claim that lost to the winning one: ESPN's FAILED_INVALIDPLAYERSOURCE, Yahoo's
# "Lower Offer" / "Lower waiver priority" (src/ingest/yahoo_faab_offers.py). A void
# claim (Yahoo's "Invalid claim due to previous transaction") was no competing bid.
LOST = {"FAILED_INVALIDPLAYERSOURCE", "LOST_LOWER_OFFER", "LOST_LOWER_PRIORITY"}


def size(text):
    """Length as GroupMe counts it, at its most cautious: UTF-16 units, so a bold
    letter (outside the basic plane) counts twice."""
    return len(text.encode("utf-16-le")) // 2


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def report(site, league, week=None):
    """(week, header, [claim lines], [players dropped]) -- or (week, None, None, None)
    when no waiver claim went through. `site` is the site's modules: its loader
    (team tags) and its standings (places, as the site shows them)."""
    load, data_path = site["load"], site["data_path"]
    teams = load(league, "team_weeks")["teams"]
    luck = load(league, "luck")
    if week is None:
        week = max(int(w) for w in luck["weeks"]) + 1
    tag = lambda tid: teams.get(str(tid), {}).get("abbrev", str(tid))   # noqa: E731

    pickups = load(league, "pickups")["teams"]
    won = [(tid, a) for tid, xs in pickups.items() for a in xs
           if int(a["week"]) == week and a["source"] == "waivers"]
    if not won:
        return week, None, None, None

    # every claim, won or lost, where the platform publishes them (ESPN): the
    # claims that lost to this one, and with FAAB the best of their bids
    lost, traditional = {}, False
    if data_path(league, "claims").exists():
        c = load(league, "claims")
        traditional = not (c.get("acquisition") or {}).get("uses_budget", True)
        for x in c["claims"]:
            if x["week"] is not None and int(x["week"]) == week and x["status"] in LOST:
                lost.setdefault(x["player"], []).append((x.get("bid") or 0, x["team_id"]))
    # traditional waivers go by the standings going into the week: the place shown
    place = site["places"](league, luck["rows"], week - 1) if traditional else {}

    spent = sum(a["bid"] or 0 for _, a in won)
    if traditional:
        won.sort(key=lambda x: (-place.get(str(x[0]), 0), x[1]["player"]))
    else:
        won.sort(key=lambda x: (-(x[1]["bid"] or 0), tag(x[0]), x[1]["player"]))

    n = len(won)
    contested = sum(1 for _, a in won if lost.get(a["player"]))
    head = (f"Week {week} Waivers are in: {plural(contested, 'contested claim')}, {plural(n, 'total claim')}"
            + (f", ${spent} spent in total." if spent else "."))
    lines, dropped = [], []
    for tid, a in won:
        pos = "" if a["pos"] in a["player"] or a["pos"] == "DST" else f" {a['pos']}"   # "Ravens D/ST", "Chiefs"
        others = lost.get(a["player"], [])
        if traditional:
            what = f" ({ordinal(place[str(tid)])})" if str(tid) in place else ""
            more = (f", {plural(len(others), 'lower claim')} ({', '.join(tag(t) for _, t in others)})"
                    if others else "")
        else:
            what = f" (${a['bid'] or 0})" if spent or a["bid"] else ""
            # the second-highest bid and whose it was (the first listed wins a tie)
            bid2, team2 = max(others, key=lambda o: o[0]) if others else (None, None)
            more = f", {plural(len(others), 'lower bid')}, ${bid2} ({tag(team2)})" if others else ""
        lines.append(f"{tag(tid)}: {a['player']}{pos}{what}{more}.")
        if a.get("dropped"):
            dropped.append(a["dropped"])
    return week, head, lines, dropped


def as_text(head, lines, dropped):
    """One GroupMe message under LIMIT. Claims are cut from the bottom with
    "...and N more" until it fits; the dropped paragraph is always whole."""
    tail = (["", "Players dropped".translate(BOLD) + ": " + ", ".join(dropped)] if dropped else [])
    for keep in range(len(lines), -1, -1):
        more = len(lines) - keep
        text = "\n".join([head] + lines[:keep] + ([f"...and {more} more"] if more else []) + tail)
        if size(text) <= LIMIT:
            return text
    raise SystemExit("The players-dropped paragraph alone is over GroupMe's limit.")


def as_html(subject, head, lines, dropped):
    e = html.escape
    items = "".join(f"<li>{e(x)}</li>" for x in lines)
    drops = f"<p style='margin:12px 0 0'><b>Players dropped:</b> {e(', '.join(dropped))}</p>" if dropped else ""
    return ("<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.4'>"
            f"<h2 style='margin:0 0 8px'>{e(subject)}</h2><p style='margin:0 0 8px'>{e(head)}</p>"
            f"<ul style='margin:0'>{items}</ul>{drops}</div>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=Path, required=True, help="a jimoody.com checkout")
    ap.add_argument("--league", required=True)
    ap.add_argument("--week", type=int, help="default: the week after the latest final week")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    os.environ.setdefault("FFF_PIPELINE_DIR", str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(a.site.resolve()))
    from leagues_site import LEAGUES, data_path, load      # the site's loader: its team tags
    from standings import places                           # and its standings

    week, head, lines, dropped = report({"load": load, "data_path": data_path, "places": places},
                                        a.league, a.week)
    a.out.mkdir(parents=True, exist_ok=True)
    for name in ("none", "waivers.txt", "waivers.html", "waivers_subject.txt"):
        (a.out / name).unlink(missing_ok=True)
    if head is None:
        (a.out / "none").write_text(str(week), encoding="utf-8")
        print(f"{a.league} week {week}: no waiver claims went through; nothing to send", file=sys.stderr)
        return
    subject = f"{LEAGUES[a.league]['label']} week {week}: waivers"
    text = as_text(head, lines, dropped)
    assert size(text) <= LIMIT
    (a.out / "waivers.txt").write_text(text, encoding="utf-8")
    (a.out / "waivers.html").write_text(as_html(subject, head, lines, dropped), encoding="utf-8")
    (a.out / "waivers_subject.txt").write_text(subject, encoding="utf-8")
    print(f"{a.league} week {week}: {len(lines)} claims, {size(text)} of {LIMIT} characters", file=sys.stderr)


if __name__ == "__main__":
    main()
