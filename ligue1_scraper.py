"""
ligue1_scraper.py — scraper pre Ligue 1 (API ma-api.ligue1.fr)
Štatistiky sa agregujú z hráčskych Opta štatistík.
Použitie:
    python ligue1_scraper.py                    # stiahne všetky zápasy sezóny 2025/26
    python ligue1_scraper.py --id <match_id>    # jeden konkrétny zápas
    python ligue1_scraper.py --force            # stiahne znovu aj existujúce
    python ligue1_scraper.py --delay 2.0        # vlastný delay medzi requestmi
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
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL       = "https://ma-api.ligue1.fr"
CHAMPIONSHIP_ID = 1   # Ligue 1 McDonald's
DATA_DIR       = Path(__file__).parent / "data" / "ligue1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/json",
    "Referer": "https://ligue1.com/",
}

# Opta stat kľúče pre agregáciu na tímovú úroveň
TEAM_STATS = [
    "total_scoring_att",
    "ontarget_scoring_att",
    "shot_off_target",
    "blocked_scoring_att",
    "won_corners",
    "corner_taken",
    "fouls",
    "was_fouled",
    "yellow_card",
    "red_card",
    "second_yellow",
    "expected_goals",
    "total_offside",
    "total_pass",
    "accurate_pass",
    "total_tackle",
    "won_tackle",
    "saves",
    "total_clearance",
    "big_chance_missed",
    "big_chance_created",
    "touches",
    "aerial_won",
    "aerial_lost",
    "interception",
    "duel_won",
    "duel_lost",
    "total_distance",
    "hit_woodwork",
]


def _get(endpoint: str) -> dict:
    resp = requests.get(BASE_URL + endpoint, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.json()


def fetch_calendar() -> dict[int, list[str]]:
    """Vráti {gameweek_number: [match_id, ...]} pre celú sezónu."""
    data = _get(f"/championship-calendar/{CHAMPIONSHIP_ID}")
    result = {}
    for gw_num, gw in data.get("gameWeeks", {}).items():
        result[int(gw_num)] = gw.get("matchesIds", [])
    return result


def _get_referee(officials: list) -> str:
    for o in officials or []:
        if isinstance(o, dict) and o.get("type") == 1:
            return o.get("name", "")
    return ""


def _aggregate_stats(players: dict) -> dict:
    """Agreguje hráčske stats do tímových — len pre hráčov čo hrali."""
    agg = {k: 0.0 for k in TEAM_STATS}
    for p in players.values():
        if not p.get("playedMatch"):
            continue
        stats = p.get("stats") or {}
        for k in TEAM_STATS:
            v = stats.get(k)
            if isinstance(v, (int, float)):
                agg[k] += v
    return agg


def _round_stat(v: float, is_float: bool = False) -> int | float | None:
    if v == 0.0:
        return None
    return round(v, 3) if is_float else int(round(v))


def parse_match(raw: dict, match_id: str) -> dict:
    home    = raw.get("home") or {}
    away    = raw.get("away") or {}
    stadium = raw.get("stadium") or {}

    date_raw = raw.get("date", "")
    try:
        date_str = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = str(date_raw)[:10]

    h_stats = _aggregate_stats(home.get("players") or {})
    a_stats = _aggregate_stats(away.get("players") or {})

    # Possession z dotykov
    h_touch = h_stats.get("touches") or 0
    a_touch = a_stats.get("touches") or 0
    total_touch = h_touch + a_touch
    home_poss = round(h_touch / total_touch * 100) if total_touch > 0 else None
    away_poss = round(a_touch / total_touch * 100) if total_touch > 0 else None

    def s(stats: dict, key: str, is_float: bool = False):
        v = stats.get(key, 0.0)
        return _round_stat(v, is_float)

    return {
        "match_id":             match_id,
        "season":               raw.get("season"),
        "date":                 date_str,
        "round":                raw.get("gameWeekNumber"),
        "status":               (raw.get("period") or "").replace("fullTime", "full_time"),
        "referee":              _get_referee(raw.get("officials")),
        "attendance":           stadium.get("attendance"),
        "stadium":              stadium.get("name", ""),
        "home_team":            (home.get("clubIdentity") or {}).get("shortName", ""),
        "away_team":            (away.get("clubIdentity") or {}).get("shortName", ""),
        "home_score":           home.get("score"),
        "away_score":           away.get("score"),
        "home_formation":       home.get("formation"),
        "away_formation":       away.get("formation"),
        "home_possession":      home_poss,
        "away_possession":      away_poss,
        "home_shots":           s(h_stats, "total_scoring_att"),
        "away_shots":           s(a_stats, "total_scoring_att"),
        "home_shots_on_target": s(h_stats, "ontarget_scoring_att"),
        "away_shots_on_target": s(a_stats, "ontarget_scoring_att"),
        "home_shots_off_target":s(h_stats, "shot_off_target"),
        "away_shots_off_target":s(a_stats, "shot_off_target"),
        "home_shots_blocked":   s(h_stats, "blocked_scoring_att"),
        "away_shots_blocked":   s(a_stats, "blocked_scoring_att"),
        "home_corners":         s(h_stats, "won_corners"),
        "away_corners":         s(a_stats, "won_corners"),
        "home_fouls":           s(h_stats, "fouls"),
        "away_fouls":           s(a_stats, "fouls"),
        "home_yellow":          s(h_stats, "yellow_card"),
        "away_yellow":          s(a_stats, "yellow_card"),
        "home_red":             s(h_stats, "red_card"),
        "away_red":             s(a_stats, "red_card"),
        "home_xg":              s(h_stats, "expected_goals", is_float=True),
        "away_xg":              s(a_stats, "expected_goals", is_float=True),
        "home_offsides":        s(h_stats, "total_offside"),
        "away_offsides":        s(a_stats, "total_offside"),
        "home_passes":          s(h_stats, "total_pass"),
        "away_passes":          s(a_stats, "total_pass"),
        "home_passes_accurate": s(h_stats, "accurate_pass"),
        "away_passes_accurate": s(a_stats, "accurate_pass"),
        "home_tackles":         s(h_stats, "total_tackle"),
        "away_tackles":         s(a_stats, "total_tackle"),
        "home_tackles_won":     s(h_stats, "won_tackle"),
        "away_tackles_won":     s(a_stats, "won_tackle"),
        "home_saves":           s(h_stats, "saves"),
        "away_saves":           s(a_stats, "saves"),
        "home_clearances":      s(h_stats, "total_clearance"),
        "away_clearances":      s(a_stats, "total_clearance"),
        "home_big_chances_missed": s(h_stats, "big_chance_missed"),
        "away_big_chances_missed": s(a_stats, "big_chance_missed"),
        "home_aerials_won":     s(h_stats, "aerial_won"),
        "away_aerials_won":     s(a_stats, "aerial_won"),
    }


def save_match(parsed: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{parsed['match_id']}.json"
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_existing() -> set[str]:
    if not DATA_DIR.exists():
        return set()
    return {p.stem for p in DATA_DIR.glob("*.json")}


def main():
    parser = argparse.ArgumentParser(description="Ligue 1 scraper")
    parser.add_argument("--id",    help="ID konkrétneho zápasu")
    parser.add_argument("--force", action="store_true", help="Stiahne znovu aj existujúce")
    parser.add_argument("--delay", type=float, default=1.2, help="Pauza medzi requestmi (default: 1.2)")
    args = parser.parse_args()

    if args.id:
        raw    = _get(f"/championship-match/{args.id}")
        parsed = parse_match(raw, args.id)
        path   = save_match(parsed)
        sys.stdout.buffer.write(json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
        logger.info("Uložené: %s", path)
        return

    logger.info("Sťahujem kalendár sezóny …")
    calendar = fetch_calendar()
    all_ids  = [mid for gw_ids in calendar.values() for mid in gw_ids]
    logger.info("Nájdených %d zápasov v %d kolách", len(all_ids), len(calendar))

    existing = set() if args.force else load_existing()
    to_fetch = [mid for mid in all_ids if mid not in existing]
    logger.info("Na stiahnutie: %d | preskočených: %d", len(to_fetch), len(existing))

    ok = fail = skip = 0
    for i, match_id in enumerate(to_fetch, 1):
        try:
            raw    = _get(f"/championship-match/{match_id}")
            period = raw.get("period", "")
            gw     = raw.get("gameWeekNumber", "?")

            parsed = parse_match(raw, match_id)
            save_match(parsed)
            if period == "fullTime":
                logger.info("[%d/%d] ✓  J%s  %s %s-%s %s  xG %.2f-%.2f",
                            i, len(to_fetch), gw,
                            parsed.get("home_team", "?"),
                            parsed.get("home_score", "?"),
                            parsed.get("away_score", "?"),
                            parsed.get("away_team", "?"),
                            parsed.get("home_xg") or 0,
                            parsed.get("away_xg") or 0)
            else:
                logger.info("[%d/%d] 📅  J%s  %s vs %s  (status: %s)",
                            i, len(to_fetch), gw,
                            parsed.get("home_team", "?"),
                            parsed.get("away_team", "?"),
                            period)
            ok += 1
        except Exception as exc:
            logger.warning("[%d/%d] ✗  %s — %s", i, len(to_fetch), match_id, exc)
            fail += 1

        if i < len(to_fetch):
            time.sleep(args.delay)

    logger.info("Hotovo: %d OK, %d preskočených, %d chýb | Dáta: %s", ok, skip, fail, DATA_DIR)


if __name__ == "__main__":
    main()
