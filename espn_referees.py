"""
espn_referees.py — doplní rozhodcov z ESPN API do matches.csv

ESPN API je verejné, nevyžaduje API kľúč.
Podporované ligy:
    por.1  — Primeira Liga
    (rozšíriteľné na ďalšie)

Použitie:
    python espn_referees.py
    python espn_referees.py --dry-run
"""

import argparse
import csv
import logging
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(encoding="utf-8")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
DATA_ROOT = Path(__file__).parent / "data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

LEAGUES = {
    "por.1": {"name": "Primeira Liga", "dir": "primeira_liga"},
}

# ESPN názvy tímov → CSV názvy
TEAM_MAP = {
    "Sporting CP":           "Sp Lisbon",
    "C.D. Nacional":         "Nacional",
    "FC Famalicao":          "Famalicao",
    "Braga":                 "Sp Braga",
    "Vitória de Guimaraes":  "Guimaraes",
    "FC Porto":              "Porto",
    "Vitória SC":            "Guimaraes",
}


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _map_team(espn_name: str, csv_teams: list[str]) -> str | None:
    if espn_name in TEAM_MAP:
        mapped = TEAM_MAP[espn_name]
        if mapped in csv_teams:
            return mapped
    n = _norm(espn_name)
    for t in csv_teams:
        if _norm(t) == n or _norm(t) in n or n in _norm(t):
            return t
    return None


# ---------------------------------------------------------------------------
# ESPN API
# ---------------------------------------------------------------------------

def get_game_ids_for_date(league_code: str, date_str: str) -> list[dict]:
    """Vráti zoznam {id, home, away, date} pre daný dátum."""
    url = f"{ESPN_BASE}/{league_code}/scoreboard?dates={date_str}&limit=20"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return []
    events = []
    for e in resp.json().get("events", []):
        comps = e.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) < 2:
            continue
        # ESPN: competitors[0] = away, competitors[1] = home (alebo naopak)
        # Použijeme homeAway flag
        home = next((c["team"]["displayName"] for c in competitors if c.get("homeAway") == "home"), "")
        away = next((c["team"]["displayName"] for c in competitors if c.get("homeAway") == "away"), "")
        events.append({"id": e["id"], "home": home, "away": away, "date": e["date"][:10]})
    return events


def get_referee(league_code: str, game_id: str) -> str:
    """Vráti meno hlavného rozhodcu pre daný game_id."""
    url = f"{ESPN_BASE}/{league_code}/summary?event={game_id}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return ""
    officials = resp.json().get("gameInfo", {}).get("officials", [])
    return officials[0]["fullName"] if officials else ""


# ---------------------------------------------------------------------------
# Hlavná logika
# ---------------------------------------------------------------------------

def patch_league(league_code: str, dry_run: bool) -> tuple[int, int, int]:
    meta = LEAGUES[league_code]
    csv_path = DATA_ROOT / meta["dir"] / "matches.csv"

    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())

    # Zisti unikátne dátumy chýbajúcich zápasov
    missing_dates = sorted(set(
        r["date"] for r in rows
        if r.get("status") == "full_time" and not r.get("referee", "").strip()
    ))
    logger.info("%s: %d zápasov bez sudcu, %d unikátnych dátumov", meta["name"],
                sum(1 for r in rows if r.get("status") == "full_time" and not r.get("referee","").strip()),
                len(missing_dates))

    if not missing_dates:
        logger.info("%s: všetko kompletné", meta["name"])
        return 0, 0, 0

    csv_teams = list({r["home_team"] for r in rows} | {r["away_team"] for r in rows})
    # Index CSV riadkov: (date, home_team) → row
    idx = {(r["date"], r["home_team"]): r for r in rows}

    filled = not_found = no_ref = 0

    for i, d in enumerate(missing_dates):
        events = get_game_ids_for_date(league_code, d.replace("-", ""))
        if not events:
            logger.debug("Žiadne zápasy na %s", d)
            continue

        for ev in events:
            csv_home = _map_team(ev["home"], csv_teams)
            if not csv_home:
                logger.debug("Neznámy tím: %s", ev["home"])
                not_found += 1
                continue

            row = idx.get((d, csv_home))
            if not row or row.get("referee", "").strip():
                continue

            referee = get_referee(league_code, ev["id"])
            if not referee:
                no_ref += 1
                continue

            if not dry_run:
                row["referee"] = referee
            filled += 1
            logger.debug("%s  %s vs %s → %s", d, csv_home, row.get("away_team",""), referee)

        time.sleep(0.4)
        if (i + 1) % 10 == 0:
            logger.info("Spracovaných %d/%d dátumov, doplnených: %d", i + 1, len(missing_dates), filled)

    if not dry_run and filled > 0:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info("%s: zapísané do %s", meta["name"], csv_path)

    return filled, not_found, no_ref


def main():
    parser = argparse.ArgumentParser(description="Doplní rozhodcov z ESPN API")
    parser.add_argument("--league", nargs="+", choices=list(LEAGUES.keys()),
                        default=list(LEAGUES.keys()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for code in args.league:
        filled, not_found, no_ref = patch_league(code, args.dry_run)
        status = "DRY RUN" if args.dry_run else "zapísané"
        logger.info("%s: doplnených=%d, neznámych tímov=%d, bez rozhodcu v ESPN=%d [%s]",
                    code, filled, not_found, no_ref, status)


if __name__ == "__main__":
    main()
