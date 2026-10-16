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
LEAGUES = {
    "adn": {
        "platform": "yahoo",
        "yahoo_league_name": "ADN Alumni",
        "label": "ADN",
        "page": "adn.html",
        "highlight": "JUST",
    },
    "college-forever": {
        "platform": "espn",
        "league_id": "1526935",
        "creds": "CF",
        "label": "College Forever",
        "page": "college-forever.html",
        "highlight": "JOHN",
        "tiebreak": "head_to_head",
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
