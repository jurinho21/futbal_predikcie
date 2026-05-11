"""
ligue1_to_csv.py — Konvertuje JSON súbory Ligue 1 do flat CSV.
Výstup: data/ligue1/matches.csv
"""

import sys
import json
import csv
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent / "data" / "ligue1"
OUT_CSV  = DATA_DIR / "matches.csv"

COLUMNS = [
    "match_id", "season", "date", "round",
    "referee", "stadium", "attendance",
    "home_team", "away_team", "home_score", "away_score",
    "home_formation", "away_formation",
    "home_possession", "away_possession",
    "home_shots", "away_shots",
    "home_shots_on_target", "away_shots_on_target",
    "home_shots_off_target", "away_shots_off_target",
    "home_shots_blocked", "away_shots_blocked",
    "home_corners", "away_corners",
    "home_fouls", "away_fouls",
    "home_yellow", "away_yellow",
    "home_red", "away_red",
    "home_xg", "away_xg",
    "home_offsides", "away_offsides",
    "home_passes", "away_passes",
    "home_passes_accurate", "away_passes_accurate",
    "home_tackles", "away_tackles",
    "home_tackles_won", "away_tackles_won",
    "home_saves", "away_saves",
    "home_clearances", "away_clearances",
    "home_big_chances_missed", "away_big_chances_missed",
    "home_aerials_won", "away_aerials_won",
    "status",
]


def main():
    files = sorted(DATA_DIR.glob("*.json"))
    print(f"Načítavam {len(files)} JSON súborov …")

    rows = []
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        row = {col: d.get(col, "") for col in COLUMNS}
        rows.append(row)

    def sort_key(r):
        try:
            rnd = int(r["round"])
        except (ValueError, TypeError):
            rnd = 999
        return (rnd, r.get("date", ""))

    rows.sort(key=sort_key)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with_stats  = sum(1 for r in rows if r["home_shots"] not in ("", None))
    with_ref    = sum(1 for r in rows if r["referee"])
    with_xg     = sum(1 for r in rows if r["home_xg"] not in ("", None))
    print(f"\nHotovo: {len(rows)} riadkov → {OUT_CSV}")
    print(f"  So štatistikami:  {with_stats}")
    print(f"  S xG:             {with_xg}")
    print(f"  So sudcom:        {with_ref}")
    print(f"  Bez štatistík:    {len(rows) - with_stats}")

    print("\nPrvých 5 riadkov:")
    for r in rows[:5]:
        print(f"  J{r['round']:>2}  {r['home_team']:20} {r['home_score']}-{r['away_score']}  "
              f"{r['away_team']:20}  shots {r['home_shots']}-{r['away_shots']}  "
              f"xG {r['home_xg']}-{r['away_xg']}")


if __name__ == "__main__":
    main()
