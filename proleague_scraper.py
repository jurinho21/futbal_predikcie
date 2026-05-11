"""
proleague_scraper.py — scraper pre Jupiler Pro League (Belgian Pro League)
Dáta z: proleague.be (__NEXT_DATA__ SSR JSON)
Použitie:
    python proleague_scraper.py                      # stiahne všetky zápasy (BFS od J30)
    python proleague_scraper.py --slug <slug>        # jeden konkrétny zápas
    python proleague_scraper.py --force              # stiahne znovu aj existujúce
"""

import sys
import json
import re
import time
import logging
import argparse
import requests
from pathlib import Path
from datetime import datetime
from collections import deque

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_URL  = "https://www.proleague.be"
MATCH_URL = BASE_URL + "/fr/matchs/{slug}"
DATA_DIR  = Path(__file__).parent / "data" / "proleague"

# ID fáz sezóny (pre calendar API ?roundId=...)
REGULAR_ROUND_ID = "2e222cc4-f3f9-4030-9356-26ed1250fe70"
PLAYOFF_ROUND_ID  = "cd071bd9-ede4-4329-9530-d4da23dc483e"

# Všetky playoff gameweek IDs (J31–J40) — API neodhalí upcoming slugy, ale
# každé kolo fetchujeme priamo aby nám neušiel žiadny zápas
PLAYOFF_GAMEWEEK_IDS = [
    "5b96e06a-c4fb-4946-99ec-2becd4900ee4",  # J31
    "a7ed4fa0-8183-452d-95e5-0952c8e0c145",  # J32
    "cf3f0ea3-7f1f-45a8-8174-bd6c5ae6224a",  # J33
    "3c24c4c8-06a8-423c-9ff2-7cfd91e87524",  # J34
    "63dc38a7-1535-4f24-a2ad-098ae1ba3c61",  # J35
    "cc1e8be3-0d5c-4c2e-b33b-628a01e9a519",  # J36
    "53e4b946-9d1b-4f3c-a514-094eef4ddd3b",  # J37
    "52d89b15-6c0d-4629-aaa7-aa9a02d4eace",  # J38
    "6a343107-fe37-4984-af73-03263b975f87",  # J39
    "b8b322cb-cf0a-465c-844c-9970a22b161e",  # J40
]

SLUG_RE = re.compile(
    r'saison-\d{4}-\d{4}-jupiler-pro-league-\d+-[a-z0-9-]+-vs-[a-z0-9-]+'
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

STAT_MAP = {
    "totalScoringAtt":      "shots",
    "ontargetScoringAtt":   "shots_on_target",
    "shotOffTarget":        "shots_off_target",
    "blockedScoringAtt":    "shots_blocked",
    "wonCorners":           "corners",
    "cornerTaken":          "corners_taken",
    "lostCorners":          "corners_conceded",
    "fkFoulLost":           "fouls",
    "fkFoulWon":            "fouls_won",
    "totalYelCard":         "yellow_cards",
    "totalRedCard":         "red_cards",
    "possessionPercentage": "possession",
    "totalPass":            "passes",
    "accuratePass":         "passes_accurate",
    "totalOffside":         "offsides",
    "wonTackle":            "tackles_won",
    "totalTackle":          "tackles",
    "saves":                "saves",
    "totalClearance":       "clearances",
    "bigChanceMissed":      "big_chances_missed",
    "goals":                "goals",
    "goalAssist":           "assists",
}


# ---------------------------------------------------------------------------
# Sieťové funkcie
# ---------------------------------------------------------------------------

def _get_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _get_build_hash(html: str) -> str | None:
    m = re.search(r'/_next/static/([^/]+)/_buildManifest', html)
    return m.group(1) if m else None


def _extract_next_data(html: str) -> dict:
    m = re.search(r'__NEXT_DATA__[^>]+>(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("__NEXT_DATA__ nenájdené")
    return json.loads(m.group(1))


def _fetch_round_slugs(build_hash: str, round_id: str | None, season: str = "2025-2026") -> tuple[list[str], list[str]]:
    """Vráti (match_slugy, next_round_ids) pre dané kolo. round_id=None = default kolo."""
    params = "?params=fr&params=jpl-calendar"
    if round_id:
        params = f"?roundId={round_id}&params=fr&params=jpl-calendar"
    url = f"{BASE_URL}/_next/data/{build_hash}/fr/jpl-calendar.json{params}"
    r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
    data = r.json()

    # Zozbieraj slugy rekurzívne z celej odpovede
    all_slugs: set[str] = set()
    _collect_slugs(data, season, all_slugs)

    # Pokús sa nájsť ID nasledujúceho kola pre ďalší fetch
    next_ids: list[str] = []
    try:
        md = data["pageProps"]["data"]["page"]["grids"][0]["areas"][0]["modules"][0]["data"]
        for key in ("nextRound", "next_round", "nextGameweek"):
            nxt = md.get(key)
            if isinstance(nxt, dict):
                for id_key in ("id", "roundId", "uuid"):
                    nid = nxt.get(id_key)
                    if nid and isinstance(nid, str):
                        next_ids.append(nid)
                        break
    except Exception:
        pass

    return list(all_slugs), next_ids


def get_seed_slugs(build_hash: str, season: str = "2025-2026") -> list[str]:
    """Vráti seed slugy zo všetkých kôl (regular season + všetky playoff gameweeks)."""
    slugs: set[str] = set()

    # Regular season a playoff fáza (vráti aktívne kolo danej fázy)
    for round_id in [None, REGULAR_ROUND_ID, PLAYOFF_ROUND_ID]:
        try:
            found, _ = _fetch_round_slugs(build_hash, round_id, season)
            slugs.update(found)
            logger.info("Fáza %s: %d slugov", round_id or "default", len(found))
        except Exception as exc:
            logger.warning("Chyba pri fáze %s: %s", round_id, exc)

    # Explicitne fetchni každé playoff kolo — API nenaviguje na budúce kolá
    for gw_id in PLAYOFF_GAMEWEEK_IDS:
        try:
            url = (f"{BASE_URL}/_next/data/{build_hash}/fr/jpl-calendar.json"
                   f"?roundId={PLAYOFF_ROUND_ID}&gameweekId={gw_id}&params=fr&params=jpl-calendar")
            r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
            found: set[str] = set()
            _collect_slugs(r.json(), season, found)
            new = found - slugs
            slugs.update(found)
            if new:
                logger.info("Playoff gameweek %s: +%d nových slugov", gw_id[:8], len(new))
        except Exception as exc:
            logger.warning("Chyba pri playoff gameweek %s: %s", gw_id[:8], exc)

    return list(slugs)


def extract_slugs_from_html(html: str, season: str = "2025-2026") -> set[str]:
    """Extrahuje všetky match slugy z HTML stránky."""
    found = set(SLUG_RE.findall(html))
    return {s for s in found if season in s}


def _collect_slugs(obj, season: str, out: set):
    """Rekurzívne prehľadá JSON štruktúru a zbiera match slugy."""
    if isinstance(obj, str):
        if season in obj and SLUG_RE.search(obj):
            out.add(SLUG_RE.search(obj).group(0))
    elif isinstance(obj, dict):
        slug = obj.get("slug")
        if isinstance(slug, str) and season in slug:
            out.add(slug)
        for v in obj.values():
            _collect_slugs(v, season, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_slugs(item, season, out)


def extract_slugs_from_next_data(html: str, season: str = "2025-2026") -> set[str]:
    """Extrahuje slugy z __NEXT_DATA__ JSON bloku — zachytí aj nadchádzajúce zápasy."""
    try:
        nd = _extract_next_data(html)
        found: set[str] = set()
        _collect_slugs(nd, season, found)
        return found
    except Exception as exc:
        logger.warning("Chyba pri parsovaní __NEXT_DATA__: %s", exc)
        return set()


# ---------------------------------------------------------------------------
# Parsovanie
# ---------------------------------------------------------------------------

def _extract_team_stats(team_stats: dict) -> dict:
    raw = team_stats.get("stats") or {}
    return {
        our_key: raw[opta_key]
        for opta_key, our_key in STAT_MAP.items()
        if raw.get(opta_key) is not None
    }


def _get_referee(referees: list) -> str:
    for r in referees or []:
        if isinstance(r, dict) and (r.get("role") or {}).get("type") == "Referee":
            return (r.get("referee") or {}).get("name", "")
    return ""


def parse_match(html: str, slug: str = "") -> dict:
    nd   = _extract_next_data(html)
    data = nd["props"]["pageProps"]["data"]
    game  = data.get("game") or {}
    stats = data.get("stats") or {}

    home_info = game.get("homeTeam") or {}
    away_info = game.get("awayTeam") or {}
    gameweek  = game.get("gameweek") or {}

    date_raw = game.get("date", "")
    try:
        date_str = datetime.fromisoformat(date_raw).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = str(date_raw)[:10]

    period = (game.get("period") or {}).get("type", "")

    return {
        "slug":           slug,
        "date":           date_str,
        "round":          gameweek.get("week"),
        "round_name":     gameweek.get("name", ""),
        "status":         period,
        "referee":        _get_referee(game.get("referees")),
        "attendance":     game.get("attendance"),
        "home_team":      home_info.get("name", ""),
        "away_team":      away_info.get("name", ""),
        "home_score":     game.get("homeScore"),
        "away_score":     game.get("awayScore"),
        "home_formation": game.get("homeFormation"),
        "away_formation": game.get("awayFormation"),
        "home":           _extract_team_stats(stats.get("homeTeamStats") or {}),
        "away":           _extract_team_stats(stats.get("awayTeamStats") or {}),
    }


# ---------------------------------------------------------------------------
# Ukladanie
# ---------------------------------------------------------------------------

def save_match(parsed: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{parsed['slug']}.json"
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_existing_slugs() -> set[str]:
    if not DATA_DIR.exists():
        return set()
    return {p.stem for p in DATA_DIR.glob("*.json")}


def load_unfinished_slugs() -> set[str]:
    """Vráti slugy uložených zápasov, ktoré ešte neboli odohraté (na re-fetch)."""
    unfinished = set()
    for p in DATA_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("home_score") is None:
                unfinished.add(p.stem)
        except Exception:
            pass
    return unfinished


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Jupiler Pro League scraper")
    parser.add_argument("--slug",  help="Slug konkrétneho zápasu")
    parser.add_argument("--force", action="store_true",
                        help="Stiahne znovu aj existujúce zápasy")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Pauza medzi requestmi v sekundách (default: 1.5)")
    parser.add_argument("--season", default="2025-2026",
                        help="Sezóna na filtrovanie slugov (default: 2025-2026)")
    args = parser.parse_args()

    if args.slug:
        html   = _get_html(MATCH_URL.format(slug=args.slug))
        parsed = parse_match(html, slug=args.slug)
        path   = save_match(parsed)
        sys.stdout.buffer.write(
            json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        logger.info("Uložené: %s", path)
        return

    # --- BFS crawler ---
    logger.info("Zisťujem build hash a seed slugy …")
    try:
        cal_html   = _get_html(BASE_URL + "/fr/jpl-calendar")
        build_hash = _get_build_hash(cal_html)
        seeds = get_seed_slugs(build_hash) if build_hash else []
        # Extrahuj slugy z __NEXT_DATA__ kalendára — zachytí aj nadchádzajúce kolá
        seeds = list(set(seeds) | extract_slugs_from_next_data(cal_html, args.season))
    except Exception as exc:
        logger.warning("Seed chyba: %s — štartujem bez seedov", exc)
        seeds = []

    existing   = set() if args.force else load_existing_slugs()
    unfinished = set() if args.force else load_unfinished_slugs()
    # Nesplnené zápasy treba re-fetchovať — vyraď ich zo „skip" množiny
    to_skip = existing - unfinished

    # Inicializuj BFS frontu
    discovered: set[str] = set(seeds) | existing
    queue: deque[str] = deque(s for s in seeds if s not in to_skip)
    # Pridaj aj uložené nesplnené — potrebujeme ich re-fetchovať
    for s in unfinished:
        if s not in {x for x in queue}:
            queue.append(s)

    logger.info("Seed slugy: %d | Existujúce: %d | Na re-fetch (nesplnené): %d",
                len(seeds), len(existing), len(unfinished))

    ok, fail, skipped = 0, 0, 0

    while queue:
        slug = queue.popleft()

        if slug in to_skip and not args.force:
            skipped += 1
            continue

        url   = MATCH_URL.format(slug=slug)
        label = slug[:65]
        try:
            html   = _get_html(url)
            # Objavuj nové slugy — z HTML aj z __NEXT_DATA__ JSON
            new_slugs = (
                extract_slugs_from_html(html, args.season) |
                extract_slugs_from_next_data(html, args.season)
            ) - discovered
            for ns in new_slugs:
                discovered.add(ns)
                queue.append(ns)

            parsed = parse_match(html, slug=slug)
            save_match(parsed)
            status = parsed.get("status", "")
            score  = f"{parsed.get('home_score','?')}-{parsed.get('away_score','?')}"
            logger.info("[Q:%d] ✓  %s  (%s vs %s  %s  status:%s  +%d nových)",
                        len(queue), label,
                        parsed.get("home_team","?"), parsed.get("away_team","?"),
                        score, status, len(new_slugs))
            ok += 1

        except Exception as exc:
            logger.warning("[Q:%d] ✗  %s — %s", len(queue), label, exc)
            fail += 1

        if queue:
            time.sleep(args.delay)

    logger.info("Hotovo: %d OK, %d preskočených, %d chýb | Celkom objavených: %d | Dáta: %s",
                ok, skipped, fail, len(discovered), DATA_DIR)


if __name__ == "__main__":
    main()
