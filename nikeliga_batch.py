"""
nikeliga_batch.py — dávkový scraper celej sezóny Niké ligy

Použitie:
    python nikeliga_batch.py list 2025
    python nikeliga_batch.py fetch 2025 ./data --only-finished
    python nikeliga_batch.py export-csv ./data
    python nikeliga_batch.py export-sqlite ./data ./data/nikeliga.db
    python nikeliga_batch.py all 2025 ./data
"""

import sys
import json
import re
import time
import sqlite3
import csv
import os
import argparse
import logging
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from nikeliga_scraper import fetch_html, parse_match, HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nikeliga.sk"
RATE_LIMIT_SLEEP = 1.5  # sekúnd medzi requestmi


# ---------------------------------------------------------------------------
# 1. OBJAVENIE ID ZÁPASOV
# ---------------------------------------------------------------------------

def discover_match_ids(season_year: int) -> list[dict]:
    """Vráti zoznam {id, url, home, away, date, finished} pre danú sezónu."""
    ids = []
    # fázy: 1 = základná časť, 2 = skupina o titul, 3 = o udržanie
    for stage in (1, 2, 3):
        url = f"{BASE_URL}/zapasy/{season_year}?id_round=999&id_stage={stage}"
        try:
            html = fetch_html(url)
        except requests.HTTPError as e:
            logger.warning("[stage %d] HTTP chyba: %s", stage, e)
            continue
        time.sleep(RATE_LIMIT_SLEEP)

        soup = BeautifulSoup(html, "html.parser")

        # Hľadáme linky na zápasy /zapas/XXXX-yyy-zzz
        for a in soup.select("a[href*='/zapas/']"):
            href = a["href"]
            m = re.search(r"/zapas/(\d+)-([a-z]+)-([a-z]+)", href)
            if not m:
                continue
            match_id = int(m.group(1))
            if any(x["id"] == match_id for x in ids):
                continue

            # Zisti či zápas skončil — skóre je napr. "2:1", čas je "18:00".
            # Skóre: obe čísla < 20. Čas: druhé číslo je 00/15/30/45 alebo prvé > 15.
            link_text = a.get_text(strip=True)
            score_m = re.search(r"(\d+)\s*:\s*(\d+)", link_text)
            finished = False
            if score_m:
                left, right = int(score_m.group(1)), int(score_m.group(2))
                finished = left < 20 and right < 60 and not (left > 12 and right in (0, 15, 30, 45, 59))

            # Dátum — hľadaj v najbližšom rodičovskom bloku
            container = a.find_parent("div") or a
            date_el = container.select_one("time[datetime], .date, [class*='date'], [class*='info']")
            date = ""
            if date_el:
                date = date_el.get("datetime", "") or date_el.get_text(strip=True)

            ids.append({
                "id": match_id,
                "url": BASE_URL + href if href.startswith("/") else href,
                "stage": stage,
                "date": date,
                "finished": finished,
            })

    logger.info("Nájdených %d zápasov pre sezónu %d/%d", len(ids), season_year, season_year + 1)
    return ids


# ---------------------------------------------------------------------------
# 2. DÁVKOVÉ SŤAHOVANIE
# ---------------------------------------------------------------------------

def fetch_season(
    season_year: int,
    data_dir: Path,
    only_finished: bool = True,
):
    data_dir = Path(data_dir)
    html_dir = data_dir / "html"
    json_dir = data_dir / "json"
    html_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    index_path = data_dir / "matches_index.json"

    # Načítaj alebo objav index
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            matches = json.load(f)
        logger.info("Index načítaný: %d zápasov", len(matches))
    else:
        matches = discover_match_ids(season_year)
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)

    if only_finished:
        to_fetch = [m for m in matches if m.get("finished", True)]
    else:
        to_fetch = matches

    downloaded = 0
    skipped = 0
    errors = 0

    for match in to_fetch:
        mid = match["id"]
        json_path = json_dir / f"{mid}.json"
        html_path = html_dir / f"{mid}.html"

        # Resume — preskočí už stiahnuté
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
            data["season"] = season_year

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            downloaded += 1
        except Exception as e:
            logger.error("CHYBA %d: %s", mid, e)
            errors += 1

    logger.info("Hotovo. Stiahnuté: %d, preskočené: %d, chyby: %d", downloaded, skipped, errors)
    return downloaded, skipped, errors


def refresh_upcoming(data_dir: Path, days_ahead: int = 3, days_back: int = 14) -> tuple[int, int]:
    """
    Znovu stiahne JSON súbory pre zápasy v okne <days_back> dní dozadu až <days_ahead> dní dopredu.
    Vráti (obnovené, chyby).
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
    cutoff = now + timedelta(days=days_ahead)
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

        # Preskočí odohraté zápasy s kompletnými second_half dátami
        if meta.get("home_score") is not None and _has_complete_stats(d):
            continue

        # Skontroluj dátum — obnov zápasy v okne lookback..cutoff
        date_str = meta.get("date", m.get("date", ""))
        if date_str:
            try:
                match_date = dateparser.parse(date_str, dayfirst=True)
                if match_date and not (lookback <= match_date <= cutoff):
                    continue
            except Exception:
                pass  # ak nevieme parsovať dátum, radšej obnovíme

        html_path = html_dir / f"{mid}.html"
        if html_path.exists():
            html_path.unlink()

        url = m.get("url", "")
        try:
            html = fetch_html(url)
            html_path.write_text(html, encoding="utf-8")
            data = parse_match(html, url)
            data["match_id"] = mid
            data["season"] = d.get("season")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            refreshed += 1
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            logger.error("CHYBA refresh %d: %s", mid, e)
            errors += 1

    return refreshed, errors


# ---------------------------------------------------------------------------
# 3. EXPORT CSV
# ---------------------------------------------------------------------------

def _has_complete_stats(d: dict) -> bool:
    """Vráti True ak zápas obsahuje nenulové second_half štatistiky (bol stiahnutý po konci zápasu)."""
    second_half = d.get("stats", {}).get("second_half", {})
    for val in second_half.values():
        if isinstance(val, dict):
            if any(v not in (None, 0) for v in val.values()):
                return True
    return False


def _stat_val(stats: dict, key: str, side: str) -> Optional[float]:
    # stats je teraz {total: {fouls: {home, away}, ...}, first_half: ...}
    total = stats.get("total", stats)  # fallback na flat štruktúru
    entry = total.get(key)
    if isinstance(entry, dict):
        return entry.get(side)
    return None


def export_csv(data_dir: Path):
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    matches_csv = data_dir / "matches.csv"

    match_fields = [
        "match_id", "season", "date", "round", "referee",
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

    with open(matches_csv, "w", newline="", encoding="utf-8") as mf:
        mw = csv.DictWriter(mf, fieldnames=match_fields)
        mw.writeheader()

        for json_path in sorted(json_dir.glob("*.json")):
            with open(json_path, encoding="utf-8") as f:
                d = json.load(f)

            meta = d.get("meta", {})
            if meta.get("home_score") is None:
                continue
            stats = d.get("stats", {})
            # Preskočí zápasy kde chýbajú všetky hlavné štatistiky (nedohrané/neúplné)
            total = stats.get("total", stats)
            if not any(total.get(k) for k in ("fouls", "corners", "shots_on_target")):
                continue

            raw_ref = meta.get("referee", "")
            row = {
                "match_id": d.get("match_id"),
                "season": d.get("season"),
                "date": meta.get("date", ""),
                "round": meta.get("round", ""),
                "referee": raw_ref.split(",")[0].strip(),
                "home_team": meta.get("home_team", ""),
                "away_team": meta.get("away_team", ""),
                "home_score": meta.get("home_score"),
                "away_score": meta.get("away_score"),
                "home_xg": _stat_val(stats, "xg", "home"),
                "away_xg": _stat_val(stats, "xg", "away"),
                "home_shots": _stat_val(stats, "shots", "home"),
                "away_shots": _stat_val(stats, "shots", "away"),
                "home_shots_on_target": _stat_val(stats, "shots_on_target", "home"),
                "away_shots_on_target": _stat_val(stats, "shots_on_target", "away"),
                "home_corners": _stat_val(stats, "corners", "home"),
                "away_corners": _stat_val(stats, "corners", "away"),
                "home_fouls": _stat_val(stats, "fouls", "home"),
                "away_fouls": _stat_val(stats, "fouls", "away"),
                "home_yellow": _stat_val(stats, "yellow_cards", "home"),
                "away_yellow": _stat_val(stats, "yellow_cards", "away"),
                "home_red": _stat_val(stats, "red_cards", "home"),
                "away_red": _stat_val(stats, "red_cards", "away"),
                "home_possession": _stat_val(stats, "possession", "home"),
                "away_possession": _stat_val(stats, "possession", "away"),
                "attendance": meta.get("attendance"),
            }
            mw.writerow(row)

    logger.info("CSV exportované: %s", matches_csv)


# ---------------------------------------------------------------------------
# 4. EXPORT SQLITE
# ---------------------------------------------------------------------------

def export_sqlite(data_dir: Path, db_path: Path):
    data_dir = Path(data_dir)
    matches_csv = data_dir / "matches.csv"

    if not matches_csv.exists():
        export_csv(data_dir)

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY,
        season INTEGER, date TEXT, round TEXT, referee TEXT,
        home_team TEXT, away_team TEXT,
        home_score REAL, away_score REAL,
        home_xg REAL, away_xg REAL,
        home_shots REAL, away_shots REAL,
        home_shots_on_target REAL, away_shots_on_target REAL,
        home_corners REAL, away_corners REAL,
        home_fouls REAL, away_fouls REAL,
        home_yellow REAL, away_yellow REAL,
        home_red REAL, away_red REAL,
        home_possession REAL, away_possession REAL,
        attendance INTEGER
    );
    """)

    with open(matches_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if rows:
        cols = list(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        cur.execute("DELETE FROM matches")
        for row in rows:
            vals = [row[c] if row[c] != "" else None for c in cols]
            cur.execute(f"INSERT OR REPLACE INTO matches ({','.join(cols)}) VALUES ({placeholders})", vals)

    con.commit()
    con.close()
    logger.info("SQLite exportovaný: %s", db_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    parser = argparse.ArgumentParser(description="Niké liga batch scraper")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="Zobraz ID zápasov sezóny")
    p_list.add_argument("year", type=int)

    p_fetch = sub.add_parser("fetch", help="Stiahni zápasy sezóny")
    p_fetch.add_argument("year", type=int)
    p_fetch.add_argument("data_dir")
    p_fetch.add_argument("--only-finished", action="store_true", default=False,
                         help="Stiahni len odohrate zapasy (bez nadchadzajucich)")

    p_csv = sub.add_parser("export-csv", help="Exportuj CSV")
    p_csv.add_argument("data_dir")

    p_sqlite = sub.add_parser("export-sqlite", help="Exportuj SQLite")
    p_sqlite.add_argument("data_dir")
    p_sqlite.add_argument("db_path")

    p_all = sub.add_parser("all", help="Fetch + export-csv + export-sqlite")
    p_all.add_argument("year", type=int)
    p_all.add_argument("data_dir")

    args = parser.parse_args()

    if args.cmd == "list":
        ids = discover_match_ids(args.year)
        for m in ids:
            print(m)

    elif args.cmd == "fetch":
        fetch_season(args.year, Path(args.data_dir), args.only_finished)

    elif args.cmd == "export-csv":
        export_csv(Path(args.data_dir))

    elif args.cmd == "export-sqlite":
        export_sqlite(Path(args.data_dir), Path(args.db_path))

    elif args.cmd == "all":
        d = Path(args.data_dir)
        fetch_season(args.year, d, only_finished=False)
        export_csv(d)
        export_sqlite(d, d / "nikeliga.db")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
