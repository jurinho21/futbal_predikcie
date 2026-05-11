"""
proleague_fill.py — Doplní chýbajúce zápasy Jupiler Pro League.
Stratégia: pre každé kolo kde máme aspoň 1 súbor vezme slug, stiahne stránku,
extrahuje všetkých 8 slugov pre dané kolo a stiahne chýbajúce.
"""

import sys
import json
import re
import time
import logging
import requests
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_URL  = "https://www.proleague.be"
MATCH_URL = BASE_URL + "/fr/matchs/{slug}"
DATA_DIR  = Path(__file__).parent / "data" / "proleague"

SLUG_RE = re.compile(
    r'saison-\d{4}-\d{4}-jupiler-pro-league-(\d+)-[a-z0-9-]+-vs-[a-z0-9-]+'
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
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


def _get_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _extract_next_data(html: str) -> dict:
    m = re.search(r'__NEXT_DATA__[^>]+>(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("__NEXT_DATA__ nenájdené")
    return json.loads(m.group(1))


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
    from datetime import datetime
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


def save_match(parsed: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{parsed['slug']}.json"
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def slugs_for_round(seed_slug: str, round_no: int) -> set[str]:
    """Stiahne stránku seed_slug a vráti všetky slugy pre dané kolo."""
    html = _get_html(MATCH_URL.format(slug=seed_slug))
    pattern = re.compile(
        rf'saison-\d{{4}}-\d{{4}}-jupiler-pro-league-{round_no}-[a-z0-9-]+-vs-[a-z0-9-]+'
    )
    return set(pattern.findall(html))


def main():
    existing_files = list(DATA_DIR.glob("*.json"))
    existing_slugs = {p.stem for p in existing_files}
    logger.info("Existujúcich súborov: %d", len(existing_slugs))

    # Zozbieraj seed slugy pre každé kolo
    round_seeds: dict[int, str] = {}
    for fpath in existing_files:
        d = json.loads(fpath.read_text(encoding="utf-8"))
        r = d.get("round")
        slug = d.get("slug", "")
        if r and slug and r not in round_seeds:
            round_seeds[r] = slug

    logger.info("Kolá so seedom: %s", sorted(round_seeds.keys()))
    missing_rounds = [r for r in range(1, 31) if r not in round_seeds]
    if missing_rounds:
        logger.warning("Kolá bez seedu (preskočené): %s", missing_rounds)

    # Pre každé kolo: zisti všetky slugy a stiahni chýbajúce
    all_to_fetch: list[tuple[int, str]] = []

    for round_no in sorted(round_seeds.keys()):
        seed = round_seeds[round_no]
        try:
            slugs = slugs_for_round(seed, round_no)
            missing = slugs - existing_slugs
            logger.info("J%d: %d slugov celkom, %d chýba", round_no, len(slugs), len(missing))
            for s in sorted(missing):
                all_to_fetch.append((round_no, s))
        except Exception as exc:
            logger.warning("J%d: chyba pri zisťovaní slugov — %s", round_no, exc)
        time.sleep(0.8)

    logger.info("Na stiahnutie: %d zápasov", len(all_to_fetch))

    ok = fail = skip = 0
    for i, (round_no, slug) in enumerate(all_to_fetch, 1):
        try:
            html   = _get_html(MATCH_URL.format(slug=slug))
            parsed = parse_match(html, slug=slug)
            status = parsed.get("status", "")
            if status in ("FullTime", "played", "full_time", "") or parsed.get("home_score") is not None:
                save_match(parsed)
                logger.info("[%d/%d] ✓  J%s  %s %s-%s %s",
                            i, len(all_to_fetch), round_no,
                            parsed.get("home_team", "?"), parsed.get("home_score", "?"),
                            parsed.get("away_score", "?"), parsed.get("away_team", "?"))
                ok += 1
            else:
                logger.info("[%d/%d] ⏭  J%s  %s (status: %s)", i, len(all_to_fetch), round_no, slug[-40:], status)
                skip += 1
        except Exception as exc:
            logger.warning("[%d/%d] ✗  J%s  %s — %s", i, len(all_to_fetch), round_no, slug[-40:], exc)
            fail += 1
        if i < len(all_to_fetch):
            time.sleep(1.2)

    logger.info("Hotovo: %d OK, %d preskočených, %d chýb | Dáta: %s", ok, skip, fail, DATA_DIR)


if __name__ == "__main__":
    main()
