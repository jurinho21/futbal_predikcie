"""
footballdataorg_referees.py — doplní rozhodcov z football-data.org do matches.csv

Páruje zápasy podľa dátumu + skóre. Pre prípad zhody skóre v rovnaký deň
použije fuzzy matching mena tímu ako rozlišovač.

Použitie:
    python footballdataorg_referees.py
    python footballdataorg_referees.py --dry-run
    python footballdataorg_referees.py --league PD SA
"""

import argparse
import csv
import logging
import sys
import time
import unicodedata
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "https://api.football-data.org/v4"
API_TOKEN = "8a19f4a946904e43b68b3fc87422dcc9"
DATA_ROOT = Path(__file__).parent / "data"
SEASON = 2025  # 2025/26

LEAGUES = {
    "PD":  "la_liga",
    "BL1": "bundesliga",
    "SA":  "serie_a",
    "PPL": "primeira_liga",
}

HEADERS = {"X-Auth-Token": API_TOKEN}

# Statické mapovanie mien tímov API → CSV pre problematické prípady
TEAM_MAP: dict[str, str] = {
    # La Liga
    "Club Atlético de Madrid": "Ath Madrid",
    "Athletic Club": "Ath Bilbao",
    "RCD Espanyol de Barcelona": "Espanol",
    # Bundesliga
    "FC Bayern München": "Bayern Munich",
    "Borussia Mönchengladbach": "M'gladbach",
    "FC St. Pauli 1910": "St Pauli",
    "1. FC Heidenheim 1846": "Heidenheim",
    "1. FSV Mainz 05": "Mainz",
    "Bayer 04 Leverkusen": "Leverkusen",
    # Primeira Liga
    "Sporting Clube de Braga": "Sp Braga",
    "Vitória SC": "Guimaraes",
    "FC Vizela": "Vizela",
}


# ---------------------------------------------------------------------------
# Fuzzy team name matching
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return s.lower().strip()


def _strip_suffixes(name: str) -> str:
    """Odstráni bežné prípony ako CF, FC, SC, de Madrid, atď."""
    n = _norm(name)
    for suffix in (" cf", " fc", " sc", " ac", " ud", " rc", " rcd", " cd"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    for phrase in (" de madrid", " de barcelona", " de vigo", " balompie", " de futbol"):
        n = n.replace(phrase, "")
    return n.strip()


def _team_similarity(api_name: str, csv_name: str) -> float:
    """Vráti skóre podobnosti 0–1 medzi menom tímu z API a z CSV."""
    a = _strip_suffixes(api_name)
    b = _strip_suffixes(csv_name)
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    # Spoločné slová
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    common = words_a & words_b
    return len(common) / max(len(words_a), len(words_b))


def _resolve_team(api_name: str, csv_names: list[str]) -> str | None:
    """Vráti CSV meno tímu: najprv statický mapping, inak fuzzy match."""
    if api_name in TEAM_MAP:
        mapped = TEAM_MAP[api_name]
        if mapped in csv_names:
            return mapped
    scored = [(n, _team_similarity(api_name, n)) for n in csv_names]
    best_name, best_score = max(scored, key=lambda x: x[1])
    return best_name if best_score >= 0.5 else None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_matches(code: str) -> list[dict]:
    url = f"{API_BASE}/competitions/{code}/matches?season={SEASON}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json().get("matches", [])


# ---------------------------------------------------------------------------
# Hlavná logika
# ---------------------------------------------------------------------------

def patch_league(code: str, dry_run: bool) -> tuple[int, int, int]:
    """Doplní rozhodcov pre jednu ligu. Vracia (doplnených, konflikt, nenájdených)."""
    league_dir = DATA_ROOT / LEAGUES[code]
    csv_path = league_dir / "matches.csv"

    if not csv_path.exists():
        logger.warning("%s: matches.csv neexistuje, preskakujem", code)
        return 0, 0, 0

    # Načítaj CSV
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else []

    # Indexuj CSV zápasy (odohraté) podľa dátum+skóre
    # Kľúč: (date, home_score, away_score) → [row, ...]
    played_index: dict[tuple, list] = {}
    for row in rows:
        if row.get("status") != "full_time":
            continue
        key = (row["date"], row.get("home_score", ""), row.get("away_score", ""))
        played_index.setdefault(key, []).append(row)

    all_csv_teams = list({r["home_team"] for r in rows} | {r["away_team"] for r in rows})

    # Stiahni z API
    logger.info("%s: sťahujem zápasy z football-data.org ...", code)
    api_matches = fetch_matches(code)
    api_with_ref = [m for m in api_matches if m.get("referees") and m["status"] == "FINISHED"]
    logger.info("%s: %d zápasov so sudcom v API", code, len(api_with_ref))

    filled = 0
    conflicts = 0
    not_found = 0

    for m in api_with_ref:
        referee = m["referees"][0]["name"]
        date = m["utcDate"][:10]
        home_score = str(m["score"]["fullTime"]["home"])
        away_score = str(m["score"]["fullTime"]["away"])

        key = (date, home_score, away_score)
        candidates = played_index.get(key, [])

        if not candidates:
            logger.debug("%s: nenájdený %s %s-%s v CSV", code, date, home_score, away_score)
            not_found += 1
            continue

        if len(candidates) == 1:
            target = candidates[0]
        else:
            # Viac kandidátov so rovnakým dátumom a skóre → statický mapping / fuzzy
            api_home = m["homeTeam"]["name"]
            api_away = m["awayTeam"]["name"]
            csv_home_teams = [r["home_team"] for r in candidates]

            resolved_home = _resolve_team(api_home, csv_home_teams)
            if resolved_home:
                target = next(r for r in candidates if r["home_team"] == resolved_home)
            else:
                # Fallback: kombinované skóre home + away
                def _combined(r: dict) -> float:
                    return (
                        _team_similarity(api_home, r["home_team"]) * 0.6
                        + _team_similarity(api_away, r["away_team"]) * 0.4
                    )
                best = max(candidates, key=_combined)
                score = _combined(best)
                if score < 0.3:
                    logger.warning(
                        "%s: konflikt %s %s-%s, najlepší match %.2f ('%s' vs '%s')",
                        code, date, home_score, away_score, score, api_home, best["home_team"],
                    )
                    conflicts += 1
                    continue
                target = best

        if target.get("referee"):
            continue  # už má sudcu

        if not dry_run:
            target["referee"] = referee
        filled += 1
        logger.debug(
            "%s: %s %s vs %s → %s",
            code, date, target["home_team"], target["away_team"], referee,
        )

    if not dry_run and filled > 0:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info("%s: zapísané do %s", code, csv_path)

    return filled, conflicts, not_found


def main():
    parser = argparse.ArgumentParser(description="Doplní rozhodcov z football-data.org")
    parser.add_argument(
        "--league", nargs="+", choices=list(LEAGUES.keys()),
        default=list(LEAGUES.keys()),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total_filled = 0
    for i, code in enumerate(args.league):
        filled, conflicts, not_found = patch_league(code, args.dry_run)
        status = "DRY RUN" if args.dry_run else "zapísané"
        logger.info(
            "%s: doplnených=%d, konfliktov=%d, nenájdených=%d [%s]",
            code, filled, conflicts, not_found, status,
        )
        total_filled += filled
        if i < len(args.league) - 1:
            time.sleep(7)  # max 10 calls/min

    logger.info("Hotovo. Celkovo doplnených: %d", total_filled)


if __name__ == "__main__":
    main()
