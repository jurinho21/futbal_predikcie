"""
chanceliga_batch.py — dávkový scraper Chance ligy (chanceliga.cz)

Použitie:
    python chanceliga_batch.py listing ./data          # stiahni 40 zápasov zo stránky
    python chanceliga_batch.py scan 4000 4500 ./data  # skenuj ID rozsah
    python chanceliga_batch.py export-csv ./data
    python chanceliga_batch.py all ./data              # listing + export
"""

import sys
import json
import re
import time
import csv
import argparse
import logging
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup

from chanceliga_scraper import fetch_html, parse_match, HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.chanceliga.cz"
LISTING_URL = f"{BASE_URL}/zapasy"
RATE_LIMIT_SLEEP = 1.5


# ---------------------------------------------------------------------------
# 1. OBJAVENIE ZÁPASOV
# ---------------------------------------------------------------------------

def discover_from_listing() -> list[dict]:
    """
    Stiahne stránku /zapasy a extrahuje linky na zápasy.
    Vracia max ~40 zápasov (stránka ignoruje parametre stránkovania).
    """
    try:
        html = fetch_html(LISTING_URL)
    except requests.HTTPError as e:
        logger.error("HTTP chyba pri /zapasy: %s", e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    matches = []
    seen_ids: set[int] = set()

    for a in soup.select("a[href*='/zapas/']"):
        href = a["href"]
        m = re.search(r"/zapas/(\d+)", href)
        if not m:
            continue
        mid = int(m.group(1))
        if mid in seen_ids:
            continue
        seen_ids.add(mid)

        url = BASE_URL + href if href.startswith("/") else href
        link_text = a.get_text(strip=True)
        score_m = re.search(r"(\d+)\s*:\s*(\d+)", link_text)
        finished = bool(score_m)

        matches.append({
            "id": mid,
            "url": url,
            "finished": finished,
        })

    logger.info("Zo stránky zoznam: %d zápasov", len(matches))
    return matches


def discover_by_id_range(start_id: int, end_id: int) -> list[dict]:
    """
    Prehľadá ID rozsah <start_id, end_id> a vráti existujúce zápasy.
    Skúša URL https://www.chanceliga.cz/zapas/{id} — 404 = neexistuje.
    """
    matches = []
    for mid in range(start_id, end_id + 1):
        url = f"{BASE_URL}/zapas/{mid}"
        try:
            resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code == 404:
                logger.debug("ID %d: 404, preskočené", mid)
                continue
            if resp.status_code >= 400:
                logger.debug("ID %d: HTTP %d", mid, resp.status_code)
                continue
            # Resolve slug z finálnej URL po presmerovaní
            final_url = resp.url
            matches.append({"id": mid, "url": final_url, "finished": True})
            logger.info("ID %d: nájdené → %s", mid, final_url)
            time.sleep(RATE_LIMIT_SLEEP)
        except requests.RequestException as e:
            logger.warning("ID %d: chyba %s", mid, e)

    logger.info("Sken %d–%d: nájdených %d zápasov", start_id, end_id, len(matches))
    return matches


# ---------------------------------------------------------------------------
# 2. DÁVKOVÉ SŤAHOVANIE
# ---------------------------------------------------------------------------

def _load_blacklist(data_dir: Path) -> set:
    path = Path(data_dir) / "blacklist.json"
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {str(x) for x in json.load(f)}


def fetch_matches(
    matches: list[dict],
    data_dir: Path,
    only_finished: bool = True,
) -> tuple[int, int, int]:
    """
    Stiahne a sparsuje zápasy zo zoznamu.
    Vracia (stiahnuté, preskočené, chyby).
    """
    data_dir = Path(data_dir)
    html_dir = data_dir / "html"
    json_dir = data_dir / "json"
    html_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    if only_finished:
        to_fetch = [m for m in matches if m.get("finished", True)]
    else:
        to_fetch = matches

    blacklist = _load_blacklist(data_dir)
    downloaded = skipped = errors = 0

    for match in to_fetch:
        mid = match["id"]
        if str(mid) in blacklist:
            skipped += 1
            continue
        json_path = json_dir / f"{mid}.json"
        html_path = html_dir / f"{mid}.html"

        if json_path.exists():
            skipped += 1
            continue

        url = match["url"]
        logger.info("Sťahujem %d: %s", mid, url)

        try:
            if html_path.exists():
                html = html_path.read_text(encoding="utf-8")
            else:
                html = fetch_html(url)
                html_path.write_text(html, encoding="utf-8")
                time.sleep(RATE_LIMIT_SLEEP)

            data = parse_match(html, url)
            data["match_id"] = mid

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            downloaded += 1
        except Exception as e:
            logger.error("CHYBA %d: %s", mid, e)
            errors += 1

    logger.info("Hotovo. Stiahnuté: %d, preskočené: %d, chyby: %d",
                downloaded, skipped, errors)
    return downloaded, skipped, errors


def _has_placeholder_teams(meta: dict) -> bool:
    """Vráti True ak zápas má placeholder mená tímov (napr. 'vítěz 7/10')."""
    for key in ("home_team", "away_team"):
        val = meta.get(key, "")
        if val and val.startswith("vítěz"):
            return True
    return False


def refresh_recent(data_dir: Path, days_back: int = 14) -> tuple[int, int]:
    """
    Znovu stiahne JSON pre:
      - MINULÉ zápasy bez výsledku (home_score = None), v okne posledných <days_back> dní
      - BUDÚCE zápasy s placeholder názvami tímov (napr. 'vítěz 7/10'), kým nie sú tímy známe
    Vracia (obnovené, chyby).
    """
    from datetime import datetime, timedelta
    from dateutil import parser as dateparser

    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    html_dir = data_dir / "html"
    index_path = data_dir / "matches_index.json"

    if not index_path.exists():
        return 0, 0

    with open(index_path, encoding="utf-8") as f:
        matches = json.load(f)

    now = datetime.now()
    lookback = now - timedelta(days=days_back)
    refreshed = 0
    errors = 0

    for m in matches:
        mid = m["id"]
        json_path = json_dir / f"{mid}.json"

        if not json_path.exists():
            continue

        with open(json_path, encoding="utf-8") as f:
            d = json.load(f)

        meta = d.get("meta", {})

        # Preskočí zápasy, ktoré už majú výsledok aj skutočné mená tímov
        if meta.get("home_score") is not None and not _has_placeholder_teams(meta):
            continue

        date_str = meta.get("date", m.get("date", ""))
        if date_str:
            try:
                match_date = dateparser.parse(date_str, dayfirst=True)
                if match_date:
                    is_future = match_date > now
                    in_lookback_window = lookback <= match_date <= now
                    # Budúce zápasy s placeholder tímami: refreshni kým tímy nie sú známe
                    if is_future and _has_placeholder_teams(meta):
                        pass  # pokračuj — treba aktualizovať mená
                    # Minulé zápasy bez výsledku: len v lookback okne
                    elif not is_future and in_lookback_window:
                        pass  # pokračuj
                    else:
                        continue
            except Exception:
                pass
        elif not date_str:
            # Ak nie je dátum, preskočíme — nepoznáme kedy sa hral
            continue

        html_path = html_dir / f"{mid}.html"
        if html_path.exists():
            html_path.unlink()

        url = m.get("url", "")
        try:
            html = fetch_html(url)
            html_path.write_text(html, encoding="utf-8")
            data = parse_match(html, url)
            data["match_id"] = mid
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            refreshed += 1
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            logger.error("CHYBA refresh %d: %s", mid, e)
            errors += 1

    return refreshed, errors


def _load_or_create_index(data_dir: Path, matches: list[dict]) -> list[dict]:
    index_path = data_dir / "matches_index.json"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            existing = {m["id"]: m for m in json.load(f)}
        for m in matches:
            existing.setdefault(m["id"], m)
        merged = list(existing.values())
    else:
        merged = matches

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


# ---------------------------------------------------------------------------
# 3. EXPORT CSV
# ---------------------------------------------------------------------------

def _season_from_date(date_str: str) -> Optional[int]:
    try:
        parts = date_str.split(" ")[0].split("/")
        month, year = int(parts[1]), int(parts[2])
        return year if month >= 7 else year - 1
    except Exception:
        return None


def _stat_val(stats: dict, key: str, side: str) -> Optional[float]:
    total = stats.get("total", stats)
    entry = total.get(key)
    if isinstance(entry, dict):
        return entry.get(side)
    return None


def export_csv(data_dir: Path):
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    matches_csv = data_dir / "matches.csv"

    fields = [
        "match_id", "date", "season", "round", "referee",
        "home_team", "away_team", "home_score", "away_score",
        "home_xg", "away_xg",
        "home_shots", "away_shots",
        "home_shots_on_target", "away_shots_on_target",
        "home_corners", "away_corners",
        "home_fouls", "away_fouls",
        "home_yellow", "away_yellow",
        "home_red", "away_red",
        "home_possession", "away_possession",
        "attendance",
    ]

    rows_written = 0
    with open(matches_csv, "w", newline="", encoding="utf-8") as mf:
        writer = csv.DictWriter(mf, fieldnames=fields)
        writer.writeheader()

        blacklist = _load_blacklist(data_dir)
        for json_path in sorted(json_dir.glob("*.json")):
            if json_path.stem in blacklist:
                continue
            try:
                with open(json_path, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception as e:
                logger.warning("Preskočený %s: %s", json_path.name, e)
                continue

            meta = d.get("meta", {})
            if meta.get("home_score") is None:
                continue
            stats = d.get("stats", {})
            # Preskočí zápasy kde chýbajú všetky hlavné štatistiky (nedohrané/neúplné)
            total = stats.get("total", stats)
            if not any(total.get(k) for k in ("fouls", "corners", "shots_on_target")):
                continue

            writer.writerow({
                "match_id":           d.get("match_id"),
                "date":               meta.get("date", ""),
                "season":             _season_from_date(meta.get("date", "")),
                "round":              meta.get("round", ""),
                "referee":            meta.get("referee", ""),
                "home_team":          meta.get("home_team", ""),
                "away_team":          meta.get("away_team", ""),
                "home_score":         meta.get("home_score"),
                "away_score":         meta.get("away_score"),
                "home_xg":            _stat_val(stats, "xg",               "home"),
                "away_xg":            _stat_val(stats, "xg",               "away"),
                "home_shots":         _stat_val(stats, "shots",            "home"),
                "away_shots":         _stat_val(stats, "shots",            "away"),
                "home_shots_on_target": _stat_val(stats, "shots_on_target","home"),
                "away_shots_on_target": _stat_val(stats, "shots_on_target","away"),
                "home_corners":       _stat_val(stats, "corners",          "home"),
                "away_corners":       _stat_val(stats, "corners",          "away"),
                "home_fouls":         _stat_val(stats, "fouls",            "home"),
                "away_fouls":         _stat_val(stats, "fouls",            "away"),
                "home_yellow":        _stat_val(stats, "yellow_cards",     "home"),
                "away_yellow":        _stat_val(stats, "yellow_cards",     "away"),
                "home_red":           _stat_val(stats, "red_cards",        "home"),
                "away_red":           _stat_val(stats, "red_cards",        "away"),
                "home_possession":    _stat_val(stats, "possession",       "home"),
                "away_possession":    _stat_val(stats, "possession",       "away"),
                "attendance":         meta.get("attendance"),
            })
            rows_written += 1

    logger.info("CSV exportovaný: %s (%d riadkov)", matches_csv, rows_written)

    overrides_path = data_dir / "overrides.csv"
    if overrides_path.exists():
        df = pd.read_csv(matches_csv)
        ov = pd.read_csv(overrides_path)
        for _, row in ov.iterrows():
            mask = df["match_id"] == row["match_id"]
            for col in ov.columns:
                if col == "match_id":
                    continue
                if col in df.columns:
                    df.loc[mask & df[col].isna(), col] = row[col]
        df.to_csv(matches_csv, index=False)
        logger.info("Overrides aplikované z %s", overrides_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Chance liga batch scraper")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("listing", help="Stiahni zápasy zo zoznamu /zapasy")
    p_list.add_argument("data_dir")
    p_list.add_argument("--all", action="store_true", help="Stiahni aj neodohraté")

    p_scan = sub.add_parser("scan", help="Skenuj rozsah ID")
    p_scan.add_argument("start_id", type=int)
    p_scan.add_argument("end_id", type=int)
    p_scan.add_argument("data_dir")

    p_csv = sub.add_parser("export-csv", help="Exportuj CSV zo JSON súborov")
    p_csv.add_argument("data_dir")

    p_all = sub.add_parser("all", help="listing + export-csv")
    p_all.add_argument("data_dir")
    p_all.add_argument("--all", action="store_true")

    args = parser.parse_args()

    if args.cmd == "listing":
        matches = discover_from_listing()
        d = Path(args.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        merged = _load_or_create_index(d, matches)
        fetch_matches(merged, d, only_finished=not args.all)

    elif args.cmd == "scan":
        matches = discover_by_id_range(args.start_id, args.end_id)
        d = Path(args.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        merged = _load_or_create_index(d, matches)
        fetch_matches(merged, d, only_finished=False)

    elif args.cmd == "export-csv":
        export_csv(Path(args.data_dir))

    elif args.cmd == "all":
        d = Path(args.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        matches = discover_from_listing()
        merged = _load_or_create_index(d, matches)
        fetch_matches(merged, d, only_finished=not args.all)
        export_csv(d)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
