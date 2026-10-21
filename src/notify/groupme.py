"""Post one message to a GroupMe group through its bot.

    python -m src.notify.groupme --text-file out/waivers.txt

The bot id comes from GROUPME_BOT_ID (dev.groupme.com/bots), never the command
line. Prints only that it posted: the logs are public.
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


def post(text: str) -> None:
    bot = os.environ.get("GROUPME_BOT_ID", "").strip()
    if not bot:
        raise SystemExit("GROUPME_BOT_ID must be set.")
    if len(text) > LIMIT:
        raise SystemExit(f"Message is {len(text)} characters; GroupMe takes {LIMIT}.")
    body = json.dumps({"bot_id": bot, "text": text}).encode("utf-8")
    req = urllib.request.Request(GROUPME, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 201, 202):
            raise SystemExit(f"GroupMe answered {r.status}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", type=Path, required=True)
    text = ap.parse_args().text_file.read_text(encoding="utf-8")
    post(text)
    print(f"posted {len(text)} characters", file=sys.stderr)


if __name__ == "__main__":
    main()
