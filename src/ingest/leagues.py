"""Every ESPN league the pipeline knows about.

League IDs are not secret -- they are in every league URL -- so they live in
code. The cookies that authorize reads ARE secret and stay in .env, named by the
`creds` prefix: a league with creds "CF" reads ESPN_S2_CF and ESPN_SWID_CF.

`page` is the file in the site repo that league's renderers write into.
"""

from __future__ import annotations

LEAGUES = {
    "college-forever": {
        "league_id": "1526935",
        "creds": "CF",
        "label": "College Forever",
        "page": "adnfantasy.html",
    },
    "carrboro": {
        "league_id": "142788",
        "creds": "FL",
        "label": "Carrboro",
        "page": "carrboro.html",
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


def selected(arg: str | None) -> list[str]:
    """--league value -> keys to run. No flag means every league."""
    if arg is None:
        return list(LEAGUES)
    league(arg)          # validate
    return [arg]
