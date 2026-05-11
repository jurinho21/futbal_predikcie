"""
eredivisie_to_csv.py — Konvertuje JSON súbory Eredivisie do flat CSV.
Pridá číslo kola z fixtures.json (chýba v match JSON).
Výstup: data/eredivisie/matches.csv
"""

import sys
import json
import csv
import glob
import requests
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent / "data" / "eredivisie"
OUT_CSV  = DATA_DIR / "matches.csv"

FIXTURES_URL = "https://eredivisie.eu/cache/site/EredivisieEN/json/fixtures.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":    "application/json",
    "Referer":   "https://eredivisie.eu/",
}

COLUMNS = [
    "match_id", "season", "date", "round",
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
    "home_goals_conceded", "away_goals_conceded",
    "home_fouls_won", "away_fouls_won",
    "home_assists", "away_assists",
    "home_substitutions", "away_substitutions",
    "home_formation", "away_formation",
    "attendance", "status",
]


def _v(stats: dict, key: str):
    """Vráti total hodnotu štatistiky (nested alebo priama)."""
    val = stats.get(key)
    if val is None:
        return ""
    if isinstance(val, dict):
        v = val.get("total")
        return "" if v is None else v
    return val


def build_oid_round_map() -> dict:
    print("Sťahujem fixtures.json pre čísla kôl …")
    data = requests.get(FIXTURES_URL, headers=HEADERS, timeout=20).json()
    mapping = {}
    for rk, rd in data.items():
        if not isinstance(rd, dict):
            continue
        for m in rd.get("matches", []):
            if m.get("oid"):
                mapping[m["oid"]] = m.get("round") or rk
    print(f"  → {len(mapping)} zápasov v fixtures")
    return mapping


def main():
    oid_round = build_oid_round_map()

    files = sorted(glob.glob(str(DATA_DIR / "*.json")))
    print(f"Načítavam {len(files)} JSON súborov …")

    rows = []
    missing_round = 0

    for fpath in files:
        d = json.load(open(fpath, encoding="utf-8"))
        if d.get("status") != "full_time":
            continue
        oid = d.get("oid", Path(fpath).stem)

        round_no = oid_round.get(oid)
        if round_no is None:
            missing_round += 1

        h = d.get("home") or {}
        a = d.get("away") or {}

        row = {
            "match_id":             oid,
            "season":               "2025/26",
            "date":                 d.get("date", ""),
            "round":                round_no or "",
            "referee":              d.get("referee", ""),
            "home_team":            d.get("home_team", ""),
            "away_team":            d.get("away_team", ""),
            "home_score":           d.get("home_score", ""),
            "away_score":           d.get("away_score", ""),
            "home_shots":           _v(h, "shots"),
            "away_shots":           _v(a, "shots"),
            "home_shots_on_target": _v(h, "shots_on_target"),
            "away_shots_on_target": _v(a, "shots_on_target"),
            "home_shots_off_target":_v(h, "shots_off_target"),
            "away_shots_off_target":_v(a, "shots_off_target"),
            "home_shots_blocked":   _v(h, "shots_blocked"),
            "away_shots_blocked":   _v(a, "shots_blocked"),
            "home_corners":         _v(h, "corners"),
            "away_corners":         _v(a, "corners"),
            "home_fouls":           _v(h, "fouls"),
            "away_fouls":           _v(a, "fouls"),
            "home_yellow":          _v(h, "yellow_cards"),
            "away_yellow":          _v(a, "yellow_cards"),
            "home_red":             _v(h, "red_cards"),
            "away_red":             _v(a, "red_cards"),
            "home_possession":      _v(h, "possession"),
            "away_possession":      _v(a, "possession"),
            "home_passes":          _v(h, "passes"),
            "away_passes":          _v(a, "passes"),
            "home_passes_accurate": _v(h, "passes_accurate"),
            "away_passes_accurate": _v(a, "passes_accurate"),
            "home_tackles":         _v(h, "tackles"),
            "away_tackles":         _v(a, "tackles"),
            "home_tackles_won":     _v(h, "tackles_won"),
            "away_tackles_won":     _v(a, "tackles_won"),
            "home_saves":           _v(h, "saves"),
            "away_saves":           _v(a, "saves"),
            "home_offsides":        _v(h, "offsides"),
            "away_offsides":        _v(a, "offsides"),
            "home_clearances":      _v(h, "clearances"),
            "away_clearances":      _v(a, "clearances"),
            "home_goals_conceded":  _v(h, "goals_conceded"),
            "away_goals_conceded":  _v(a, "goals_conceded"),
            "home_fouls_won":       _v(h, "fouls_won"),
            "away_fouls_won":       _v(a, "fouls_won"),
            "home_assists":         _v(h, "assists"),
            "away_assists":         _v(a, "assists"),
            "home_substitutions":   _v(h, "substitutions"),
            "away_substitutions":   _v(a, "substitutions"),
            "home_formation":       _v(h, "formation"),
            "away_formation":       _v(a, "formation"),
            "attendance":           d.get("attendance", ""),
            "status":               d.get("status", ""),
        }
        rows.append(row)

    # Zoraď podľa kola a dátumu
    def sort_key(r):
        try:
            rnd = int(r["round"])
        except (ValueError, TypeError):
            rnd = 999
        return (rnd, r["date"])

    rows.sort(key=sort_key)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    complete = sum(1 for r in rows if r["home_shots"] != "")
    print(f"\nHotovo: {len(rows)} riadkov → {OUT_CSV}")
    print(f"  Bez kola:          {missing_round}")
    print(f"  So štatistikami:   {complete}")
    print(f"  Bez štatistík:     {len(rows) - complete}")

    # Ukáž prvých 5 riadkov
    print("\nPrvých 5 riadkov:")
    for r in rows[:5]:
        print(f"  J{r['round']:>2}  {r['home_team']:20} {r['home_score']}-{r['away_score']}  {r['away_team']:20}  shots {r['home_shots']}-{r['away_shots']}")


if __name__ == "__main__":
    main()
