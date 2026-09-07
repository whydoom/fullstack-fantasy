"""Enumerate every NFL fantasy league-season the authenticated user has played in.

Yahoo's fantasy sports JSON is hostile to naive parsing:

  - Collections are serialized as objects keyed by stringified indices — "0", "1",
    "2", ... — with a sibling "count" key, not as JSON arrays. `{"0": {...}, "1":
    {...}, "count": 2}`.
  - Elements of a resource's own array-like fields mix a metadata dict (name, key,
    season, ...) with sub-resource dicts (standings, teams, ...) in the *same* list.
  - The exact shape has drifted across API versions and will keep drifting.

Index-chasing a fixed path (e.g. `data["fantasy_content"]["users"]["0"]["user"][1]
["games"]["0"]["game"][1]["leagues"]["0"]["league"][0]`) breaks the moment any of
that shifts. Instead, this module recursively walks the entire tree and pulls out
every dict that contains a `league_key`, regardless of where it's nested. That's
slower and less precise than a fixed path, but it survives schema drift — which is
the entire point of keeping a raw layer instead of parsing at ingestion time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from src.ingest.yahoo_client import YahooFantasyClient

MANIFEST_PATH = Path("data/raw/_discovery/league_manifest.json")


def _walk_for_leagues(node: Any) -> Iterator[dict[str, Any]]:
    """Recursively yield every dict in `node` that contains a `league_key` field."""
    if isinstance(node, dict):
        if "league_key" in node:
            yield node
        for value in node.values():
            yield from _walk_for_leagues(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_for_leagues(item)


def discover_leagues(client: YahooFantasyClient) -> list[dict[str, Any]]:
    """Fetch every NFL league-season for the authenticated user and return raw league dicts.

    Never hardcodes a game_key (Yahoo's per-season numeric game identifier, e.g. "423"
    for 2023 NFL) — it discovers them by asking Yahoo for every game the user has
    played, filtered to game_codes=nfl. Game keys are not stable/predictable across
    seasons and must always come from the API.
    """
    payload = client.get("users;use_login=1/games;game_codes=nfl/leagues")
    leagues = list(_walk_for_leagues(payload))

    # Deduplicate by league_key — the recursive walk can surface the same league
    # dict more than once if Yahoo nests a summary alongside a detail view.
    seen: dict[str, dict[str, Any]] = {}
    for league in leagues:
        seen.setdefault(league["league_key"], league)
    return list(seen.values())


def _first(league: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        if key in league and league[key] not in (None, ""):
            return str(league[key])
    return default


def print_manifest_table(leagues: list[dict[str, Any]]) -> None:
    columns = ["season", "league_key", "num_teams", "scoring_type", "is_finished", "name"]
    rows = [
        [
            _first(lg, "season"),
            _first(lg, "league_key"),
            _first(lg, "num_teams"),
            _first(lg, "scoring_type"),
            _first(lg, "is_finished"),
            _first(lg, "name"),
        ]
        for lg in sorted(leagues, key=lambda lg: _first(lg, "season"))
    ]

    widths = [
        max(len(columns[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(len(columns))
    ]

    def fmt_row(row: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths))

    print(fmt_row(columns))
    print(fmt_row(["-" * w for w in widths]))
    for row in rows:
        print(fmt_row(row))


def main() -> None:
    client = YahooFantasyClient()
    leagues = discover_leagues(client)
    print_manifest_table(leagues)

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(leagues, indent=2))
    print(f"\nWrote {len(leagues)} league-seasons to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
