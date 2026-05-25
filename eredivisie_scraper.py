"""
eredivisie_scraper.py — scraper pre Eredivisie (JSON API z eredivisie.eu)
Použitie:
    python eredivisie_scraper.py                     # stiahne všetky zápasy
    python eredivisie_scraper.py --oid <oid>         # jeden konkrétny zápas
    python eredivisie_scraper.py --debug <oid>       # vypíše surový JSON
    python eredivisie_scraper.py --force             # stiahne znovu aj existujúce
"""

import sys
import json
import time
import logging
import argparse
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FIXTURES_URL = "https://eredivisie.eu/cache/site/EredivisieEN/json/fixtures.json"
MATCH_BASE = (
    "https://eredivisie.nl/cache/site/EredivisieNL/json/matches"
    "/aouykkl1rt7zo06sg0kbzkbh0/{oid}.json"
)
DATA_DIR = Path(__file__).parent / "data" / "eredivisie"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://eredivisie.eu/",
}

# Mapovanie Opta stat typov na naše kľúče
STAT_MAP = {
    "accuratePass":         "passes_accurate",
    "blockedScoringAtt":    "shots_blocked",
    "wonCorners":           "corners",
    "cornerTaken":          "corners_taken",
    "fkFoulLost":           "fouls",
    "fkFoulWon":            "fouls_won",
    "goalAssist":           "assists",
    "goals":                "goals",
    "goalsConceded":        "goals_conceded",
    "lostCorners":          "corners_conceded",
    "ontargetScoringAtt":   "shots_on_target",
    "possessionPercentage": "possession",
    "saves":                "saves",
    "shotOffTarget":        "shots_off_target",
    "subsMade":             "substitutions",
    "totalClearance":       "clearances",
    "totalOffside":         "offsides",
    "totalPass":            "passes",
    "totalScoringAtt":      "shots",
    "totalTackle":          "tackles",
    "totalYellowCard":      "yellow_cards",
    "wonTackle":            "tackles_won",
    "formationUsed":        "formation",
}


# ---------------------------------------------------------------------------
# Sieťové funkcie
# ---------------------------------------------------------------------------

def _get(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_fixtures() -> list[dict]:
    """Vráti zoznam všetkých zápasov zo všetkých 34 kôl."""
    data = _get(FIXTURES_URL)
    matches = []
    for round_key, round_data in data.items():
        if not isinstance(round_data, dict):
            continue
        for m in round_data.get("matches", []):
            if not isinstance(m, dict) or not m.get("oid"):
                continue
            home = m.get("home") or {}
            away = m.get("away") or {}
            matches.append({
                "oid":    m["oid"],
                "round":  m.get("round") or round_key,
                "date":   m.get("date", "")[:10],
                "status": m.get("status", ""),
                "home":   home.get("name", ""),
                "away":   away.get("name", ""),
                "slug":   m.get("detail", ""),
            })
    return matches


def fetch_match_raw(oid: str) -> dict:
    return _get(MATCH_BASE.format(oid=oid))


# ---------------------------------------------------------------------------
# Parsovanie
# ---------------------------------------------------------------------------

def _extract_stats(team_stats_list: list) -> dict:
    """Prevedie pole [{type, first_half, second_half, total}] na slovník."""
    result = {}
    for item in team_stats_list:
        stat_type = item.get("type")
        if not stat_type:
            continue
        key = STAT_MAP.get(stat_type, stat_type)
        result[key] = {
            "total":       item.get("total"),
            "first_half":  item.get("first_half"),
            "second_half": item.get("second_half"),
        }
    return result


def _parse_goals(goals_list: list, home_oid: str) -> list[dict]:
    parsed = []
    for g in goals_list:
        if not isinstance(g, dict):
            continue
        player = g.get("player") or {}
        assist = g.get("assist") or {}
        parsed.append({
            "minute": g.get("scored_minute"),
            "team":   "home" if g.get("team") == home_oid else "away",
            "player": player.get("name", ""),
            "assist": assist.get("name", ""),
            "type":   g.get("type", "goal"),
            "period": g.get("period"),
        })
    return parsed


def parse_match(raw: dict, oid: str = "", slug: str = "") -> dict:
    home_info = raw.get("home") or {}
    away_info = raw.get("away") or {}
    stats     = raw.get("stats") or {}

    home_stats = _extract_stats(
        (stats.get("home_team") or {}).get("team_stats", [])
    )
    away_stats = _extract_stats(
        (stats.get("away_team") or {}).get("team_stats", [])
    )

    date_raw = raw.get("date", "")
    try:
        date_str = datetime.fromisoformat(date_raw).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = str(date_raw)[:10]

    officials = raw.get("officials") or []
    referee = next(
        (f"{o.get('given_name', '')} {o.get('family_name', '')}".strip()
         for o in officials if isinstance(o, dict) and o.get("type") == "Main"),
        ""
    )

    raw_status = raw.get("progress") or raw.get("status")
    has_stats = bool((stats.get("home_team") or {}).get("team_stats"))
    if (home_info.get("score") is not None and
            away_info.get("score") is not None and has_stats):
        computed_status = "full_time"
    else:
        computed_status = raw_status

    return {
        "oid":        oid,
        "slug":       slug,
        "date":       date_str,
        "status":     computed_status,
        "attendance": raw.get("attendance"),
        "referee":    referee,
        "home_team":  home_info.get("name", ""),
        "away_team":  away_info.get("name", ""),
        "home_score": home_info.get("score"),
        "away_score": away_info.get("score"),
        "home":       home_stats,
        "away":       away_stats,
        "goals":      _parse_goals(raw.get("goals") or [], home_info.get("oid", "")),
    }


# ---------------------------------------------------------------------------
# Ukladanie
# ---------------------------------------------------------------------------

def save_match(parsed: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{parsed['oid']}.json"
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_existing_oids() -> set[str]:
    if not DATA_DIR.exists():
        return set()
    return {p.stem for p in DATA_DIR.glob("*.json")}


def load_unfinished_oids() -> set[str]:
    """Vráti OID uložených zápasov, ktoré ešte neboli odohraté (na re-fetch)."""
    unfinished = set()
    for p in DATA_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            status = (d.get("status") or "").lower()
            if d.get("home_score") is None or status not in ("full_time", "played"):
                unfinished.add(p.stem)
        except Exception:
            pass
    return unfinished


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Eredivisie scraper")
    parser.add_argument("--oid",   help="OID konkrétneho zápasu")
    parser.add_argument("--debug", metavar="OID", help="Vypíše surový JSON")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Pauza medzi requestmi v sekundách (default: 1.5)")
    parser.add_argument("--force", action="store_true",
                        help="Stiahne znovu aj už existujúce zápasy")
    args = parser.parse_args()

    if args.debug:
        raw = fetch_match_raw(args.debug)
        sys.stdout.buffer.write(
            json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        return

    if args.oid:
        raw    = fetch_match_raw(args.oid)
        parsed = parse_match(raw, oid=args.oid)
        path   = save_match(parsed)
        sys.stdout.buffer.write(
            json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        logger.info("Uložené: %s", path)
        return

    # --- Stiahni všetky zápasy ---
    logger.info("Sťahujem fixtures.json …")
    fixtures = fetch_fixtures()
    logger.info("Nájdených %d zápasov v 34 kolách", len(fixtures))

    existing   = set() if args.force else load_existing_oids()
    unfinished = set() if args.force else load_unfinished_oids()
    to_skip    = existing - unfinished
    to_fetch   = [f for f in fixtures if f["oid"] not in to_skip]
    skipped    = len(fixtures) - len(to_fetch)
    logger.info("Na stiahnutie: %d  |  preskočených (existujú): %d", len(to_fetch), skipped)

    ok, fail = 0, 0
    for i, fix in enumerate(to_fetch, 1):
        oid   = fix["oid"]
        label = f"kolo {fix.get('round','?'):>2}  {fix.get('home','?')} vs {fix.get('away','?')}"
        try:
            raw    = fetch_match_raw(oid)
            parsed = parse_match(raw, oid=oid, slug=fix.get("slug", ""))
            if not parsed.get("date") or parsed["date"].startswith("None"):
                parsed["date"] = fix.get("date", "")
            save_match(parsed)
            logger.info("[%d/%d] ✓  %s", i, len(to_fetch), label)
            ok += 1
        except Exception as exc:
            logger.warning("[%d/%d] ✗  %s — %s", i, len(to_fetch), label, exc)
            fail += 1
        if i < len(to_fetch):
            time.sleep(args.delay)

    logger.info("Hotovo: %d OK, %d chýb | Dáta: %s", ok, fail, DATA_DIR)


if __name__ == "__main__":
    main()
