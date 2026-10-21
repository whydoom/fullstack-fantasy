"""Call a fantasy game over while the NFL week is still being played.

    python -m src.notify.live_final --site ../jimoody.com --league adn --called state.json --out out/adn
    python -m src.notify.live_final --league adn --week-only        # prints the live week

A game is OVER when the team behind cannot catch up even if every one of its
players still to play scores his most:

  - a player is still to play until his NFL team's game is final (in progress
    counts as still to play, at his full most);
  - his most is 50 if he is top 5 at his position this season (jimoody.com's
    season ranks), 30 otherwise;
  - the leading team's players still to play count for nothing: it is much easier
    to score 0 than 50.

So a team with no one left and behind is over; a team behind with players left is
over only when the gap is bigger than all their mosts together.

Live data, read-only: Yahoo's scoreboard and each team's starters with their week
points (OAuth, as the pipeline already does), and ESPN's public NFL scoreboard for
which games are final (no sign-in). A team missing from the week's NFL games is on
bye: its players count as done.

Writes DIR/message.txt and DIR/subject.txt when a game was newly called, and the
updated `--called` file (the games called so far this week, so none is called
twice). Prints only counts: the logs are public. A network hiccup skips this
check (exit 0) -- the next one, ten minutes on, tries again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

LIMIT = 1000                         # GroupMe's cap on one message
BENCH = {"BN", "IR", "IR+", "NA"}
NFL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
YAHOO_TO_ESPN = {"WAS": "WSH"}       # the one team code the two spell differently
TOP_N, TOP_MOST, MOST = 5, 50, 30


def flat(node) -> dict:
    """Yahoo nests a player (or team) as lists of one-key dicts: merge them."""
    out = {}
    if isinstance(node, dict):
        out.update(node)
    elif isinstance(node, list):
        for x in node:
            out.update(flat(x))
    return out


def entries(collection: dict, key: str) -> list:
    """Yahoo's {"0": {key: ...}, "1": ..., "count": n} as a list of the values."""
    return [v[key] for k, v in sorted(collection.items(), key=lambda kv: kv[0])
            if k != "count" and isinstance(v, dict) and key in v]


def find(node, key):
    """The first value under `key` anywhere in a Yahoo payload."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for x in node:
            hit = find(x, key)
            if hit is not None:
                return hit
    return None


def nfl_states(season: int, week: int) -> dict[str, str]:
    """{NFL team code: "pre" | "in" | "post"} for the week's games."""
    r = requests.get(NFL, params={"seasontype": 2, "week": week, "dates": season}, timeout=30)
    r.raise_for_status()
    out = {}
    for e in r.json().get("events", []):
        state = e["status"]["type"]["state"]
        for c in e["competitions"][0]["competitors"]:
            out[c["team"]["abbreviation"].upper()] = state
    return out


def yahoo_week(client, league_key: str) -> tuple[int, int, str, list]:
    """(season, week, status, [(team key, points), (team key, points)] per game)."""
    sb = client.get(f"league/{league_key}/scoreboard")
    league = flat(find(sb, "league"))
    week = int(find(sb, "week"))
    status = find(sb, "status") or ""
    games = []
    for m in entries(find(sb, "matchups"), "matchup"):
        teams = entries(find(m, "teams"), "team")
        games.append([(flat(t)["team_key"], float(flat(t)["team_points"]["total"])) for t in teams])
    return int(league.get("season", 0)), week, status, games


def starters(client, team_key: str, week: int) -> list[dict]:
    d = client.get(f"team/{team_key}/roster;week={week}/players/stats;type=week;week={week}")
    out = []
    for p in entries(find(d, "players"), "player"):
        f = flat(p)
        slot = next((x["position"] for x in f.get("selected_position", []) if "position" in x), "BN")
        if slot in BENCH:
            continue
        out.append({"key": f["player_key"], "name": f.get("name", {}).get("full", "?"),
                    "nfl": f.get("editorial_team_abbr", "").upper(),
                    "pts": float((f.get("player_points") or {}).get("total") or 0)})
    return out


def season_ranks(site: Path, league: str) -> tuple[dict, dict]:
    """({player id: season rank at his position}, {team id: tag}) from jimoody.com."""
    os.environ["FFF_DATA"] = "fixtures"
    sys.path.insert(0, str(site.resolve()))
    from leagues_site import load
    tw = load(league, "team_weeks")
    ranks = {p["id"]: (p.get("season") or {}).get("rank")
             for ps in tw["grid"].values() for p in ps}
    return ranks, {tid: t["abbrev"] for tid, t in tw["teams"].items()}


def most(player: dict, ranks: dict) -> int:
    r = ranks.get(player["key"])
    return TOP_MOST if r is not None and r <= TOP_N else MOST


def call(games, rosters, states, ranks, tags) -> list[tuple[str, str]]:
    """[(game id, line)] for every game that is over."""
    tid = lambda key: key.split(".t.")[-1]   # noqa: E731
    out = []
    for (ka, pa), (kb, pb) in games:
        if pa == pb:
            continue
        (lead, lp), (trail, tp) = ((ka, pa), (kb, pb)) if pa > pb else ((kb, pb), (ka, pa))
        left = [p for p in rosters[trail]
                if states.get(YAHOO_TO_ESPN.get(p["nfl"], p["nfl"])) in ("pre", "in")]
        gap = lp - tp
        if sum(most(p, ranks) for p in left) >= gap:
            continue
        lt, tt = tags.get(tid(lead), tid(lead)), tags.get(tid(trail), tid(trail))
        line = f"{lt} {lp:.1f}, {tt} {tp:.1f}. "
        if not left:
            line += f"{tt} has no one left to play."
        else:
            names = " and ".join(p["name"] for p in left)
            line += f"{tt} trails by {gap:.1f} with only {names} left to play."
        out.append(("-".join(sorted((tid(ka), tid(kb)))), line))
    return out


def message(lines: list[str]) -> str:
    head = "This one's over!" if len(lines) == 1 else "These are over!"
    text = "\n".join([head] + [f"- {x}" for x in lines])
    return text if len(text.encode("utf-16-le")) // 2 <= LIMIT else text[:LIMIT - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    ap.add_argument("--site", type=Path)
    ap.add_argument("--called", type=Path, help="JSON: {week, games already called}")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--week-only", action="store_true")
    a = ap.parse_args()

    from src.ingest.discover import latest_league_key
    from src.ingest.leagues import LEAGUES
    from src.ingest.yahoo_client import YahooFantasyClient
    if LEAGUES[a.league]["platform"] != "yahoo":
        raise SystemExit(f"{a.league}: live calls read Yahoo only so far")
    league_key = latest_league_key(LEAGUES[a.league]["yahoo_league_name"])
    client = YahooFantasyClient()
    try:
        season, week, status, games = yahoo_week(client, league_key)
        if a.week_only:
            print(f"{season}-w{week}")
            return
        states = nfl_states(season, week)
        rosters = {k: starters(client, k, week) for g in games for k, _ in g}
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        # a hiccup in a live feed: skip this check, the next one tries again
        print(f"{a.league}: live data unavailable this time ({type(e).__name__}); skipped", file=sys.stderr)
        return

    ranks, tags = season_ranks(a.site, a.league)
    state = json.loads(a.called.read_text(encoding="utf-8")) if a.called and a.called.exists() else {}
    done = set(state.get("games", [])) if state.get("week") == week else set()
    new = [(gid, line) for gid, line in call(games, rosters, states, ranks, tags) if gid not in done]

    a.out.mkdir(parents=True, exist_ok=True)
    for name in ("message.txt", "subject.txt"):
        (a.out / name).unlink(missing_ok=True)
    if new:
        (a.out / "message.txt").write_text(message([line for _, line in new]), encoding="utf-8")
        label = LEAGUES[a.league]["label"]
        (a.out / "subject.txt").write_text(f"{label} week {week}: " + ("this one's over" if len(new) == 1
                                           else f"{len(new)} games over"), encoding="utf-8")
    if a.called:
        a.called.parent.mkdir(parents=True, exist_ok=True)
        a.called.write_text(json.dumps({"week": week, "games": sorted(done | {g for g, _ in new})}),
                            encoding="utf-8")
    live = sum(1 for s in states.values() if s == "in")
    print(f"{a.league} week {week}: {len(games)} games, {len(done)} called before, {len(new)} newly over; "
          f"{live} NFL teams playing now", file=sys.stderr)


if __name__ == "__main__":
    main()
