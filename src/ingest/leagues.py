"""Every league the pipeline knows about, on either platform.

`platform` says which ingestion path a league takes:

  espn   League IDs are not secret -- they are in every league URL -- so they
         live in code. The cookies that authorize reads ARE secret and stay in
         .env, named by the `creds` prefix: a league with creds "CF" reads
         ESPN_S2_CF and ESPN_SWID_CF.

  yahoo  Identified by league name, not key. Yahoo's league_key embeds a game key
         that changes every season, so it is resolved at run time from the
         discovery manifest (src/ingest/discover.py), never stored here.

`page` is the file in the site repo that league's renderers write into.
"""

from __future__ import annotations

# Order is display order: the site's league switcher lists leagues as they
# appear here.
#
# `median_game`: the league also scores every team against the week's median, so
# a team's standings record is its head-to-head record plus its vs-median record.
# `highlight`: the team abbreviation the site highlights by default.
# `tiebreak`: how teams level on record are ordered -- "points_for" (the
# default) or "head_to_head" (their games against each other, then points for).
# `standings`: "platform" makes the site's current standings mirror the
# platform's own order (standings_espn.py: ESPN applies divisions and its own
# tiebreakers); earlier weeks still use `tiebreak`.
# `zones`: what a place in the standings is playing for, drawn as outlined boxes
# around the site's team picker (ordered by standings). Each is a label and a
# range of places, `last` counting from the bottom (1 = last place), or `most_pf`
# for the team with the most points for wherever it stands. Ranges nest: a
# first-round bye sits inside the playoffs. `tone` fills those teams' buttons:
# "green", "yellow", "red" or "blue" (a team takes its narrowest zone's tone; a
# most_pf tone wins over any other, the team keeping its place).
# `notify`: the scheduled sends this league gets, by name -- a send left out is off
# (src/notify/plan.py reads it; the workflows ask it before sending):
#   monday_talking_points   Monday 11:07 ET: the games going into Monday night
#   tuesday_congrats        after the Tuesday refresh: congrats to the week's winners
#   tuesday_recap           after the Tuesday refresh: the week in review (recap.tuesday)
#   wednesday_waivers       Wednesday, after waivers clear
#   live_final              Sunday afternoon to Monday night: a game called over
#                           as soon as it is decided (src/notify/live_final.py)
# Each is {"via": "email" | "groupme", "to": the Actions secret holding the address
# or bot id}. Addresses never live here: this repo is public.
LEAGUES = {
    "adn": {
        "platform": "yahoo",
        "yahoo_league_name": "ADN Alumni",
        "label": "ADN",
        "page": "adn.html",
        "highlight": "JUST",
        "zones": [
            {"label": "1st-round bye", "from": 1, "to": 1, "tone": "green"},
            {"label": "Playoffs", "from": 1, "to": 7, "tone": "yellow"},
            {"label": "Most PF", "most_pf": True, "tone": "blue"},
            {"label": "Loser punishment", "last": 1, "tone": "red"},
        ],
        "notify": {
            "monday_talking_points": {"via": "email", "to": "MAIL_TO"},
            "tuesday_congrats": {"via": "groupme", "to": "GROUPME_BOT_ID"},
            "wednesday_waivers": {"via": "groupme", "to": "GROUPME_BOT_ID"},
            # testing by email to Justin; switch to {"via": "groupme", "to": "GROUPME_BOT_ID"}
            "live_final": {"via": "email", "to": "SMTP_USER"},
        },
    },
    "college-forever": {
        "platform": "espn",
        "league_id": "1526935",
        "creds": "CF",
        "label": "College Forever",
        "page": "college-forever.html",
        "highlight": "JOHN",
        "tiebreak": "head_to_head",
        "standings": "platform",
        # ESPN seeds the two division winners first, so places 1-2 are the byes
        "zones": [
            {"label": "1st-round bye", "from": 1, "to": 2, "tone": "green"},
            {"label": "Playoffs", "from": 1, "to": 6, "tone": "yellow"},
        ],
        "notify": {
            "tuesday_recap": {"via": "email", "to": "MAIL_TO_CF"},
            "wednesday_waivers": {"via": "email", "to": "MAIL_TO_CF"},
        },
    },
    "carrboro": {
        "platform": "espn",
        "league_id": "142788",
        "creds": "FL",
        "label": "Carrboro",
        "page": "carrboro.html",
        "median_game": True,
        "highlight": "MURT",
    },
}

# What a bare EspnClient() means, so the one-off probe scripts keep working.
# The extractors never rely on it: they always pass a key.
DEFAULT_LEAGUE = "college-forever"


def league(key: str) -> dict:
    try:
        return LEAGUES[key]
    except KeyError:
        raise SystemExit(
            f"Unknown league {key!r}. Known leagues: {', '.join(LEAGUES)}"
        ) from None


def keys(platform: str | None = None) -> list[str]:
    """League keys, optionally only one platform's."""
    return [k for k, cfg in LEAGUES.items()
            if platform is None or cfg["platform"] == platform]


def selected(arg: str | None, platform: str | None = None) -> list[str]:
    """--league value -> keys to run. No flag means every league (on `platform`)."""
    if arg is None:
        return keys(platform)
    cfg = league(arg)    # validate
    if platform is not None and cfg["platform"] != platform:
        raise SystemExit(f"{arg!r} is a {cfg['platform']} league, not {platform}.")
    return [arg]
