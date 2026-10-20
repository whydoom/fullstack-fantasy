"""Which league gets which scheduled send: the `notify` switchboard in
src/ingest/leagues.py, for the workflows to ask before they send.

    python -m src.notify.plan tuesday_recap          # "league SECRET" per league it is on for
    python -m src.notify.plan tuesday_congrats adn   # exit 0 if on for adn, 1 if off

Prints league keys and secret names only, never an address: the logs are public.
"""

from __future__ import annotations

import sys

from src.ingest.leagues import LEAGUES

SENDS = ("monday_talking_points", "tuesday_congrats", "tuesday_recap", "wednesday_waivers")


def on(send: str) -> list[tuple[str, dict]]:
    """[(league, {"via", "to"})] for every league that has `send` switched on."""
    if send not in SENDS:
        raise SystemExit(f"Unknown send {send!r}. Known: {', '.join(SENDS)}")
    return [(key, cfg["notify"][send]) for key, cfg in LEAGUES.items()
            if send in (cfg.get("notify") or {})]


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    leagues = on(sys.argv[1])
    if len(sys.argv) == 3:
        sys.exit(0 if any(k == sys.argv[2] for k, _ in leagues) else 1)
    for key, how in leagues:
        print(key, how["via"], how["to"])


if __name__ == "__main__":
    main()
