"""Build the player name table: one row per player who has been on a fantasy roster
this season, with his id and name on every platform, and a custom name you control.

    python build_player_names.py

Writes transform/seeds/player_names.csv (a dbt seed):

    gsis_id, espn_id, yahoo_id, position, nflverse_name, espn_name, yahoo_name,
    display_name, match_key, matched_by

  display_name   the name to show. Pre-filled with the platform name the first time a
                 player appears; YOURS to edit after that -- a rebuild keeps every
                 display_name already in the file (matched by gsis id, else by the
                 platform ids), so edits survive.
  match_key      the name normalized for matching across platforms: lowercase, with
                 Jr. / Sr. / II / III / IV / V dropped and periods, apostrophes and
                 hyphens removed ("Kenneth Walker III" and "D'Andre Swift" ->
                 "kenneth walker", "dandre swift").
  matched_by     how the row found its nflverse (gsis) id: "id" through the player ID
                 crosswalk, "name" through match_key (+ position) against the
                 crosswalk or this season's nflverse box scores when the crosswalk
                 has no id for him yet (rookies, mostly), or blank if neither did.
  team           team defences only: the NFL team abbreviation. A defence has no
                 player id, and ESPN ("49ers D/ST") and Yahoo ("49ers") name it
                 differently; one row per team holds both.

Inputs: every league's team_weeks grid (data/processed/{league}/), the latest player
ID crosswalk and this season's player box scores (data/raw/nflverse/), nflverse's team
list (for defence abbreviations). Platform placeholders like "player 3116406" are never
used as a name.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

import nflreadpy as nfl
import polars as pl

from src.ingest.leagues import LEAGUES
from src.ingest.nflverse import latest_crosswalk_path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "transform" / "seeds" / "player_names.csv"
FIELDS = ["gsis_id", "espn_id", "yahoo_id", "position", "team", "nflverse_name", "espn_name", "yahoo_name",
          "display_name", "match_key", "matched_by"]
# A team defence has no player id; it is one row per NFL team, keyed by `team`.
DST = "DST"
# IDP slots hold any defensive position nflverse names more precisely.
IDP_POSITIONS = {"LB", "ILB", "OLB", "MLB", "DE", "DT", "DL", "NT", "CB", "S", "FS", "SS", "DB"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def match_key(name: str) -> str:
    s = (name or "").lower().replace("-", " ")
    s = re.sub(r"[.'’`]", "", s)
    words = [w for w in s.split() if w not in SUFFIXES]
    return " ".join(words)


def real(name):
    """A platform's name for a player, or "" for a placeholder ("player 3116406")."""
    return "" if not name or re.fullmatch(r"player \d+", name) else name


def strip_dst(name):
    return re.sub(r"\s*D/ST$", "", name)


def max_season():
    return max(int(f.stem) for f in (ROOT / "data" / "raw" / "nflverse" / "player_stats_weekly").glob("*.parquet"))


def rostered():
    """{(platform, platform id): (name, position)} for every player on any league grid."""
    out = {}
    # the site's exports, and an export folder FFF_PROCESSED_DIR points at (the
    # Monday talking points' week in progress, with this week's new pickups)
    folders = [ROOT / "data" / "processed"]
    if os.environ.get("FFF_PROCESSED_DIR"):
        folders.append(Path(os.environ["FFF_PROCESSED_DIR"]))
    for folder in folders:
        for key, cfg in LEAGUES.items():
            path = folder / key / f"{cfg['platform']}_team_weeks.json"
            if not path.exists():
                continue
            tw = json.loads(path.read_text(encoding="utf-8"))
            for ps in tw["grid"].values():
                for p in ps:
                    pid = str(p["id"]).split(".p.")[-1]       # Yahoo ids arrive as 470.p.33389
                    out[(cfg["platform"], pid)] = (p["name"], p["pos"])
    return out


def main():
    xw = pl.read_parquet(latest_crosswalk_path()).select("gsis_id", "espn_id", "yahoo_id", "name", "position")
    by_espn, by_yahoo, by_key = {}, {}, {}
    for g, e, y, name, pos in xw.iter_rows():
        if not g:
            continue
        rec = {"gsis_id": g, "nflverse_name": name, "pos": pos}
        if e:
            by_espn[str(int(e))] = rec
        if y:
            by_yahoo[str(int(y))] = rec
        by_key.setdefault((match_key(name), pos), []).append(rec)

    # Second source of ids by name: everyone with a box score this season (rookies
    # appear here before the crosswalk catches up).
    stats = pl.read_parquet(ROOT / "data" / "raw" / "nflverse" / "player_stats_weekly" / f"{max_season()}.parquet",
                            columns=["player_id", "player_display_name", "position"]).unique()
    by_stat_key = {}
    for g, name, pos in stats.iter_rows():
        if g and name:
            by_stat_key.setdefault(match_key(name), []).append({"gsis_id": g, "nflverse_name": name, "pos": pos})
    # current franchises only: nflverse's team list keeps relocated ones (OAK, STL, SD)
    sched = pl.read_parquet(ROOT / "data" / "raw" / "nflverse" / "schedules" / f"{max_season()}.parquet",
                            columns=["home_team"])
    current = set(sched["home_team"].to_list())
    nick_to_abbr = {match_key(n): a for a, n in nfl.load_teams().select("team_abbr", "team_nick").iter_rows()
                    if a in current}

    def by_name(name, pos):
        """A unique nflverse player for this name: same position (any defensive
        position for an IDP slot), crosswalk first, then this season's box scores."""
        k = match_key(name)
        for pool in (by_key.get((k, pos), []), by_stat_key.get(k, [])):
            fits = [r for r in pool if r["pos"] == pos or (pos == "IDP" and r["pos"] in IDP_POSITIONS)]
            if len({r["gsis_id"] for r in fits}) == 1:
                return fits[0]
        return None

    rows = {}       # row key -> row
    for (platform, pid), (name, pos) in sorted(rostered().items()):
        name = real(name)
        if pos == DST:
            team = nick_to_abbr.get(match_key(strip_dst(name)), "")
            row = rows.setdefault(f"DST:{team or name}", {f: "" for f in FIELDS})
            row.update({"position": DST, "team": team, f"{platform}_id": pid, f"{platform}_name": name})
            row["display_name"] = row["display_name"] or strip_dst(name)
            continue
        rec = (by_espn if platform == "espn" else by_yahoo).get(pid)
        how = "id" if rec else ""
        if not rec and name:
            rec = by_name(name, pos)
            how = "name" if rec else ""
        key = rec["gsis_id"] if rec else f"{platform}:{pid}"
        row = rows.setdefault(key, {f: "" for f in FIELDS})
        row["gsis_id"] = rec["gsis_id"] if rec else row["gsis_id"]
        row["nflverse_name"] = rec["nflverse_name"] if rec else row["nflverse_name"]
        row["position"] = row["position"] or pos
        row[f"{platform}_id"] = pid
        row[f"{platform}_name"] = name or row[f"{platform}_name"]
        row["matched_by"] = row["matched_by"] or how

    # keep every display_name already chosen
    kept = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                for k in (r["gsis_id"], f"espn:{r['espn_id']}", f"yahoo:{r['yahoo_id']}"):
                    if k and not k.endswith(":") and r["display_name"]:
                        kept[k] = r["display_name"]
    for row in rows.values():
        row["display_name"] = next((kept[k] for k in (row["gsis_id"], f"espn:{row['espn_id']}",
                                                      f"yahoo:{row['yahoo_id']}") if k in kept),
                                   row["display_name"] or row["espn_name"] or row["yahoo_name"]
                                   or row["nflverse_name"])
        row["match_key"] = match_key(row["display_name"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["position"], r["match_key"])))
    players = [r for r in rows.values() if r["position"] != DST]
    by_how = {h: sum(1 for r in players if r["matched_by"] == h) for h in ("id", "name", "")}
    print(f"player_names: {len(players)} players + {len(rows) - len(players)} team defences -> "
          f"{OUT.relative_to(ROOT)} (gsis id by crosswalk id {by_how['id']}, by name {by_how['name']}, "
          f"none {by_how['']})")


if __name__ == "__main__":
    main()
