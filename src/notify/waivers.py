"""The Wednesday waivers report: the claims that went through overnight, for a
league's GroupMe or email.

    python -m src.notify.waivers --site ../jimoody.com --league adn --out out/adn
    python -m src.notify.waivers --site ../jimoody.com --league adn --week 6 --out x   # a past week

Reads the league's exported pickups (and claims and FAAB, where the league has
them) through jimoody.com's own loader, so team tags match the site. The week is
the one after the latest final week: Wednesday's claims are for the coming games.

Writes DIR/waivers.txt (one GroupMe message, under its 1,000-character cap),
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
FOOTER = "Every pickup: jimoody.com"


def money(bid):
    return f"${bid}" if bid else "$0"


def report(load, data_path, league, week=None):
    """(week, header lines, claim lines, footer lines), or (week, None, None, None)
    when no waiver claim went through. `load`/`data_path` are the site's."""
    teams = load(league, "team_weeks")["teams"]
    if week is None:
        week = max(int(w) for w in load(league, "luck")["weeks"]) + 1
    tag = lambda tid: teams.get(str(tid), {}).get("abbrev", str(tid))   # noqa: E731

    pickups = load(league, "pickups")["teams"]
    claims = [(tid, a) for tid, xs in pickups.items() for a in xs
              if int(a["week"]) == week and a["source"] == "waivers"]
    if not claims:
        return week, None, None, None
    claims.sort(key=lambda x: (-(x[1]["bid"] or 0), tag(x[0]), x[1]["player"]))

    spent = sum(a["bid"] or 0 for _, a in claims)
    n = len(claims)
    head = [f"Week {week} waivers are in: {n} claim{'s' if n != 1 else ''}"
            + (f", ${spent} spent." if spent else ".")]
    lines = []
    for tid, a in claims:
        bid = f" ({money(a['bid'])})" if spent else ""
        drop = f", dropped {a['dropped']}" if a.get("dropped") else ""
        pos = "" if a["pos"] in a["player"] or a["pos"] == "DST" else f" {a['pos']}"   # "Ravens D/ST", "Chiefs"
        lines.append(f"- {tag(tid)}: {a['player']}{pos}{bid}{drop}")

    foot = []
    if spent:
        tid, a = claims[0]
        foot.append(f"Biggest splash: {tag(tid)}, {money(a['bid'])} on {a['player']}.")
    # who else wanted him, where the league's failed claims are in the data
    if data_path(league, "claims").exists():
        by_player = {}
        for c in load(league, "claims")["claims"]:
            if int(c["week"]) == week:
                by_player.setdefault(c["player"], []).append(c)
        wanted = [(len({c["team_id"] for c in cs}), p, cs) for p, cs in by_player.items()]
        wanted = [w for w in wanted if w[0] > 1]
        if wanted:
            k, p, cs = max(wanted, key=lambda w: w[0])
            won = next((c for c in cs if c["won"]), None)
            foot.append(f"Most wanted: {p}, claimed by {k} teams"
                        + (f", went to {tag(won['team_id'])}." if won else "."))
    if data_path(league, "faab").exists():
        f = load(league, "faab")
        budget = f.get("budget")
        if budget:
            left = {ab: budget - (t.get("counter_spent") or 0) for ab, t in f["teams"].items()}
            top = max(left, key=left.get)
            foot.append(f"Most FAAB left: {top}, ${left[top]}.")
    foot.append(FOOTER)
    return week, head, lines, foot


def as_text(head, lines, foot):
    """One message under LIMIT: claims are cut from the bottom (smallest bids)
    with a count, never the header or the footer."""
    shown = list(lines)
    while True:
        more = len(lines) - len(shown)
        body = head + shown + ([f"- ...and {more} more"] if more else []) + foot
        text = "\n".join(body)
        if len(text) <= LIMIT or not shown:
            return text[:LIMIT]
        shown.pop()


def as_html(subject, head, lines, foot):
    e = html.escape
    items = "".join(f"<li>{e(x[2:])}</li>" for x in lines)
    return ("<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.4'>"
            f"<h2 style='margin:0 0 8px'>{e(subject)}</h2>"
            + "".join(f"<p style='margin:0 0 8px'>{e(x)}</p>" for x in head)
            + f"<ul style='margin:0 0 8px'>{items}</ul>"
            + "".join(f"<p style='margin:4px 0'>{e(x)}</p>" for x in foot[:-1])
            + "<p style='color:#666;font-size:12px;margin-top:16px'>Every pickup: "
              "<a href='https://jimoody.com'>jimoody.com</a></p></div>")


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

    week, head, lines, foot = report(load, data_path, a.league, a.week)
    a.out.mkdir(parents=True, exist_ok=True)
    for name in ("none", "waivers.txt", "waivers.html", "waivers_subject.txt"):
        (a.out / name).unlink(missing_ok=True)
    if head is None:
        (a.out / "none").write_text(str(week), encoding="utf-8")
        print(f"{a.league} week {week}: no waiver claims went through; nothing to send", file=sys.stderr)
        return
    subject = f"{LEAGUES[a.league]['label']} week {week}: waivers"
    (a.out / "waivers.txt").write_text(as_text(head, lines, foot), encoding="utf-8")
    (a.out / "waivers.html").write_text(as_html(subject, head, lines, foot), encoding="utf-8")
    (a.out / "waivers_subject.txt").write_text(subject, encoding="utf-8")
    print(f"{a.league} week {week}: {len(lines)} claims", file=sys.stderr)


if __name__ == "__main__":
    main()
