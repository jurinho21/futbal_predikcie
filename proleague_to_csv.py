"""
proleague_to_csv.py — Konvertuje JSON súbory Jupiler Pro League do flat CSV.
Výstup: data/proleague/matches.csv
"""

import sys
import json
import csv
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent / "data" / "proleague"
OUT_CSV  = DATA_DIR / "matches.csv"

COLUMNS = [
    "match_id", "season", "date", "round", "round_name",
    "referee", "home_team", "away_team", "home_score", "away_score",
    "home_shots", "away_shots",
    "home_shots_on_target", "away_shots_on_target",
    "home_shots_off_target", "away_shots_off_target",
    "home_shots_blocked", "away_shots_blocked",
    "home_corners", "away_corners",
    "home_fouls", "away_fouls",
    "home_yellow", "away_yellow",
    "home_red", "away_red",
    "home_possession", "away_possession",
    "home_passes", "away_passes",
    "home_passes_accurate", "away_passes_accurate",
    "home_tackles", "away_tackles",
    "home_tackles_won", "away_tackles_won",
    "home_saves", "away_saves",
    "home_offsides", "away_offsides",
    "home_clearances", "away_clearances",
    "home_fouls_won", "away_fouls_won",
    "home_assists", "away_assists",
    "home_big_chances_missed", "away_big_chances_missed",
    "home_formation", "away_formation",
    "attendance", "status",
]


def _v(stats: dict, key: str):
    val = stats.get(key)
    return "" if val is None else val


def main():
    files = sorted(DATA_DIR.glob("*.json"))
    print(f"Načítavam {len(files)} JSON súborov …")

    rows = []
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        h = d.get("home") or {}
        a = d.get("away") or {}

        row = {
            "match_id":               d.get("slug", path.stem),
            "season":                 "2025/26",
            "date":                   d.get("date", ""),
            "round":                  d.get("round", ""),
            "round_name":             d.get("round_name", ""),
            "referee":                d.get("referee", ""),
            "home_team":              d.get("home_team", ""),
            "away_team":              d.get("away_team", ""),
            "home_score":             d.get("home_score", ""),
            "away_score":             d.get("away_score", ""),
            "home_shots":             _v(h, "shots"),
            "away_shots":             _v(a, "shots"),
            "home_shots_on_target":   _v(h, "shots_on_target"),
            "away_shots_on_target":   _v(a, "shots_on_target"),
            "home_shots_off_target":  _v(h, "shots_off_target"),
            "away_shots_off_target":  _v(a, "shots_off_target"),
            "home_shots_blocked":     _v(h, "shots_blocked"),
            "away_shots_blocked":     _v(a, "shots_blocked"),
            "home_corners":           _v(h, "corners"),
            "away_corners":           _v(a, "corners"),
            "home_fouls":             _v(h, "fouls"),
            "away_fouls":             _v(a, "fouls"),
            "home_yellow":            _v(h, "yellow_cards"),
            "away_yellow":            _v(a, "yellow_cards"),
            "home_red":               _v(h, "red_cards"),
            "away_red":               _v(a, "red_cards"),
            "home_possession":        _v(h, "possession"),
            "away_possession":        _v(a, "possession"),
            "home_passes":            _v(h, "passes"),
            "away_passes":            _v(a, "passes"),
            "home_passes_accurate":   _v(h, "passes_accurate"),
            "away_passes_accurate":   _v(a, "passes_accurate"),
            "home_tackles":           _v(h, "tackles"),
            "away_tackles":           _v(a, "tackles"),
            "home_tackles_won":       _v(h, "tackles_won"),
            "away_tackles_won":       _v(a, "tackles_won"),
            "home_saves":             _v(h, "saves"),
            "away_saves":             _v(a, "saves"),
            "home_offsides":          _v(h, "offsides"),
            "away_offsides":          _v(a, "offsides"),
            "home_clearances":        _v(h, "clearances"),
            "away_clearances":        _v(a, "clearances"),
            "home_fouls_won":         _v(h, "fouls_won"),
            "away_fouls_won":         _v(a, "fouls_won"),
            "home_assists":           _v(h, "assists"),
            "away_assists":           _v(a, "assists"),
            "home_big_chances_missed":_v(h, "big_chances_missed"),
            "away_big_chances_missed":_v(a, "big_chances_missed"),
            "home_formation":         d.get("home_formation", ""),
            "away_formation":         d.get("away_formation", ""),
            "attendance":             d.get("attendance", ""),
            "status":                 d.get("status", ""),
        }
        rows.append(row)

    rows.sort(key=lambda r: (r["round"] or 999, r["date"]))

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with_stats = sum(1 for r in rows if r["home_shots"] != "")
    with_ref   = sum(1 for r in rows if r["referee"])
    print(f"Hotovo: {len(rows)} riadkov → {OUT_CSV}")
    print(f"  So štatistikami: {with_stats}")
    print(f"  So sudcom:       {with_ref}")
    print(f"  Bez štatistík:   {len(rows) - with_stats}")

    print("\nPrvých 5 riadkov:")
    for r in rows[:5]:
        print(f"  J{r['round']:>2}  {r['home_team']:25} {r['home_score']}-{r['away_score']}  "
              f"{r['away_team']:25}  shots {r['home_shots']}-{r['away_shots']}  ref: {r['referee']}")


if __name__ == "__main__":
    main()
