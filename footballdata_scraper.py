"""
footballdata_scraper.py — sťahuje match dáta z football-data.co.uk pre 5 veľkých líg.
Výstup: data/{liga}/matches.csv  priamo, bez medzistupňových JSON súborov.

Použitie:
    python footballdata_scraper.py                    # všetky ligy, aktuálna sezóna
    python footballdata_scraper.py --league E0         # len Premier League
    python footballdata_scraper.py --league E0 D1      # viac líg naraz
    python footballdata_scraper.py --season 2526       # konkrétna sezóna (default: 2526)
    python footballdata_scraper.py --force             # prepíše existujúce matches.csv
"""

import sys
import csv
import time
import logging
import argparse
from io import StringIO
from pathlib import Path
from http_utils import SESSION
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
DATA_ROOT = Path(__file__).parent / "data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

LEAGUES = {
    "E0":  {"name": "Premier League",  "dir": "premier_league"},
    "D1":  {"name": "Bundesliga",      "dir": "bundesliga"},
    "SP1": {"name": "La Liga",         "dir": "la_liga"},
    "I1":  {"name": "Serie A",         "dir": "serie_a"},
    "P1":  {"name": "Primeira Liga",   "dir": "primeira_liga"},
}

# Mapovanie stĺpcov football-data.co.uk → náš formát
COL_MAP = {
    "HS":       "home_shots",
    "AS":       "away_shots",
    "HST":      "home_shots_on_target",
    "AST":      "away_shots_on_target",
    "HF":       "home_fouls",
    "AF":       "away_fouls",
    "HC":       "home_corners",
    "AC":       "away_corners",
    "HY":       "home_yellow",
    "AY":       "away_yellow",
    "HR":       "home_red",
    "AR":       "away_red",
}

OUT_COLUMNS = [
    "match_id", "season", "date", "round",
    "referee", "home_team", "away_team", "home_score", "away_score",
    "home_shots", "away_shots",
    "home_shots_on_target", "away_shots_on_target",
    "home_corners", "away_corners",
    "home_fouls", "away_fouls",
    "home_yellow", "away_yellow",
    "home_red", "away_red",
    "status",
]


def _parse_date(raw: str) -> str:
    """Konvertuje DD/MM/YYYY na YYYY-MM-DD."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw.strip()


def _int_or_empty(val: str) -> str:
    v = val.strip()
    if not v:
        return ""
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return ""


def _make_match_id(code: str, date: str, home: str, away: str) -> str:
    h = home.replace(" ", "")[:8].upper()
    a = away.replace(" ", "")[:8].upper()
    d = date.replace("-", "")
    return f"{code}_{d}_{h}_{a}"


def parse_csv(content: str, code: str, season_label: str) -> list[dict]:
    reader = csv.DictReader(StringIO(content))
    rows = []
    for i, r in enumerate(reader):
        home = r.get("HomeTeam", "").strip()
        away = r.get("AwayTeam", "").strip()
        if not home or not away:
            continue

        date_raw = r.get("Date", "").strip()
        date = _parse_date(date_raw) if date_raw else ""

        fthg = r.get("FTHG", "").strip()
        ftag = r.get("FTAG", "").strip()
        is_played = bool(fthg and ftag)

        row = {
            "match_id":  _make_match_id(code, date, home, away),
            "season":    season_label,
            "date":      date,
            "round":     "",
            "referee":   r.get("Referee", "").strip(),
            "home_team": home,
            "away_team": away,
            "home_score": fthg if is_played else "",
            "away_score": ftag if is_played else "",
            "status":    "full_time" if is_played else "pre_match",
        }

        for src, dst in COL_MAP.items():
            row[dst] = _int_or_empty(r.get(src, "")) if is_played else ""

        rows.append(row)

    return rows


def fetch_league(code: str, season: str) -> str:
    url = BASE_URL.format(season=season, code=code)
    logger.info("Sťahujem %s …", url)
    resp = SESSION.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8-sig"
    return resp.text


def apply_corrections(rows: list[dict], league_dir: Path) -> int:
    """Aplikuje manuálne korekcie z corrections.json. Vracia počet aplikovaných opráv."""
    corrections_path = league_dir / "corrections.json"
    if not corrections_path.exists():
        return 0
    import json
    corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
    idx = {r["match_id"]: r for r in rows}
    applied = 0
    for c in corrections:
        match_id = c.get("match_id")
        field = c.get("field")
        value = c.get("value")
        if match_id in idx and field in OUT_COLUMNS:
            idx[match_id][field] = str(value)
            applied += 1
            logger.info("Korekcia: %s.%s = %s (%s)", match_id, field, value, c.get("note", ""))
    return applied


def save_matches(rows: list[dict], out_csv: Path):
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def preserve_referees(rows: list[dict], out_csv: Path) -> int:
    """Prenesie rozhodcov z existujúceho CSV do nových riadkov podľa match_id."""
    if not out_csv.exists():
        return 0
    with open(out_csv, encoding="utf-8", newline="") as f:
        existing = {r["match_id"]: r.get("referee", "") for r in csv.DictReader(f)}
    preserved = 0
    for row in rows:
        ref = existing.get(row["match_id"], "")
        if ref and not row.get("referee"):
            row["referee"] = ref
            preserved += 1
    return preserved


def process_league(code: str, season: str, force: bool):
    meta = LEAGUES[code]
    league_dir = DATA_ROOT / meta["dir"]
    out_csv = league_dir / "matches.csv"

    if out_csv.exists() and not force:
        logger.info("%s: matches.csv už existuje, použij --force na prepísanie", meta["name"])
        return

    content = fetch_league(code, season)
    season_label = f"20{season[:2]}/{season[2:]}"
    rows = parse_csv(content, code, season_label)

    n_preserved = preserve_referees(rows, out_csv)
    n_corrections = apply_corrections(rows, league_dir)
    save_matches(rows, out_csv)

    played   = sum(1 for r in rows if r["status"] == "full_time")
    prematch = sum(1 for r in rows if r["status"] == "pre_match")
    with_ref = sum(1 for r in rows if r.get("referee"))

    logger.info(
        "%s: %d zápasov (%d odohratých, %d naplánovaných, %d so sudcom, %d zachovaných sudcov, %d korekcií) → %s",
        meta["name"], len(rows), played, prematch, with_ref, n_preserved, n_corrections, out_csv,
    )


def main():
    parser = argparse.ArgumentParser(description="football-data.co.uk scraper")
    parser.add_argument(
        "--league", nargs="+", choices=list(LEAGUES.keys()),
        default=list(LEAGUES.keys()),
        help="Liga(y) na stiahnutie (default: všetky)",
    )
    parser.add_argument(
        "--season", default="2526",
        help="Sezóna vo formáte YYYY (napr. 2526 pre 2025/26, default: 2526)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Prepíše existujúce matches.csv",
    )
    args = parser.parse_args()

    for i, code in enumerate(args.league):
        process_league(code, args.season, args.force)
        if i < len(args.league) - 1:
            time.sleep(1.0)

    logger.info("Hotovo.")


if __name__ == "__main__":
    main()
