"""
nikeliga_tips.py — správa tipov (ukladanie, vyhodnocovanie, história)

Štruktúra tips.csv:
    tip_id, recorded_at, match_id, home_team, away_team, match_date,
    market, bet_type, line, direction,
    model_prob, model_odds, bm_odds, edge, stake,
    status, actual_value, profit
"""

import csv
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

TIPS_CSV_NAME = "tips.csv"

# market → kľúč v stats.total JSON objektu (Niké/Chance liga nested formát)
MARKET_STAT_KEY = {
    "fouls": "fouls",
    "shots_on_target": "shots_on_target",
    "corners": "corners",
    "yellow_cards": "yellow_cards",
}

# market → (home_key, away_key) pre flat JSON formát (Ligue 1)
FLAT_MARKET_STAT_KEY = {
    "fouls":           ("home_fouls",           "away_fouls"),
    "shots_on_target": ("home_shots_on_target",  "away_shots_on_target"),
    "corners":         ("home_corners",           "away_corners"),
    "yellow_cards":    ("home_yellow",            "away_yellow"),
}

# market → kľúč v d["home"] / d["away"] nested objekte (Pro League, Eredivisie)
NESTED_MARKET_STAT_KEY = {
    "fouls":           "fouls",
    "shots_on_target": "shots_on_target",
    "corners":         "corners",
    "yellow_cards":    "yellow_cards",
}

TIP_FIELDS = [
    "tip_id", "recorded_at", "match_id", "home_team", "away_team", "match_date",
    "market", "bet_type", "line", "direction",
    "model_prob", "model_odds", "bm_odds", "edge", "stake",
    "status", "actual_value", "profit",
]


def _tip_id(match_id, market, bet_type, line, direction, home_team="", away_team="") -> str:
    if str(match_id) in ("0", "", "None"):
        key = f"{home_team}|{away_team}|{market}|{bet_type}|{line}|{direction}"
    else:
        key = f"{match_id}|{market}|{bet_type}|{line}|{direction}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _to_num(v):
    if v is None:
        return None
    try:
        f = float(str(v))
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return None


def _migrate_sqlite_if_needed(data_dir: Path) -> None:
    """Raz zmigruje tips.db → tips.csv, ak CSV ešte neexistuje."""
    csv_path = Path(data_dir) / TIPS_CSV_NAME
    db_path  = Path(data_dir) / "tips.db"
    bak_path = Path(data_dir) / "tips.csv.bak"

    if csv_path.exists():
        return

    # Obnov zálohu ak existuje
    if bak_path.exists():
        bak_path.rename(csv_path)
        logger.info("Obnovená záloha: %s → %s", bak_path, csv_path)
        return

    # Zmigruj zo SQLite
    if db_path.exists():
        try:
            import sqlite3
            con = sqlite3.connect(db_path)
            rows = con.execute("SELECT * FROM tips").fetchall()
            cols = [d[0] for d in con.execute("SELECT * FROM tips LIMIT 0").description or []]
            con.close()
            if rows and cols:
                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=TIP_FIELDS)
                    writer.writeheader()
                    for row in rows:
                        d = dict(zip(cols, row))
                        writer.writerow({k: d.get(k, "") for k in TIP_FIELDS})
                logger.info("Zmigrované %d tipov zo SQLite do CSV", len(rows))
        except Exception as e:
            logger.error("Migrácia zo SQLite zlyhala: %s", e)


# ---------------------------------------------------------------------------
# VEREJNÉ API
# ---------------------------------------------------------------------------

def save_tips(data_dir: Path, tips: list[dict], stake: float = 1.0, github_token: str | None = None) -> int:
    """
    Uloží nové tipy do CSV (preskočí duplicity).
    Každý tip musí mať: match_id, home_team, away_team, match_date,
                        market, bet_type, line, direction,
                        model_prob, bm_odds, edge.
    Vracia počet novo uložených tipov.
    """
    _migrate_sqlite_if_needed(data_dir)
    tips_csv = Path(data_dir) / TIPS_CSV_NAME

    existing_ids: set[str] = set()
    if tips_csv.exists():
        with open(tips_csv, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                existing_ids.add(row["tip_id"])

    new_rows = []
    for t in tips:
        tid = _tip_id(t["match_id"], t["market"], t["bet_type"], t["line"], t["direction"],
                      t.get("home_team", ""), t.get("away_team", ""))
        if tid in existing_ids:
            continue
        model_prob = float(t["model_prob"])
        new_rows.append({
            "tip_id":       tid,
            "recorded_at":  datetime.now().strftime("%Y-%m-%d %H:%M"),
            "match_id":     t["match_id"],
            "home_team":    t["home_team"],
            "away_team":    t["away_team"],
            "match_date":   t.get("match_date", ""),
            "market":       t["market"],
            "bet_type":     t["bet_type"],
            "line":         t.get("line", ""),
            "direction":    t["direction"],
            "model_prob":   round(model_prob, 4),
            "model_odds":   round(1.0 / model_prob, 2) if model_prob > 0 else "",
            "bm_odds":      round(float(t["bm_odds"]), 2),
            "edge":         round(float(t["edge"]), 4),
            "stake":        stake,
            "status":       "pending",
            "actual_value": "",
            "profit":       "",
        })

    if not new_rows:
        return 0

    write_header = not tips_csv.exists()
    with open(tips_csv, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TIP_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    if github_token:
        try:
            from github_sync import push_tips_csv
            push_tips_csv(data_dir, github_token)
        except Exception as e:
            logger.warning("GitHub sync zlyhala: %s", e)

    return len(new_rows)


def _find_match_json(data_dir: Path, mid: str) -> Optional[Path]:
    """Nájde JSON súbor zápasu — skúsi json/ podadresár, potom priamo data_dir."""
    p = Path(data_dir) / "json" / f"{mid}.json"
    if p.exists():
        return p
    p = Path(data_dir) / f"{mid}.json"
    if p.exists():
        return p
    return None


def _find_match_json_by_teams(data_dir: Path, home: str, away: str) -> Optional[Path]:
    """Fallback pre match_id=0: hľadá JSON podľa home_team a away_team."""
    candidates = []
    for search_dir in (Path(data_dir) / "json", Path(data_dir)):
        if not search_dir.exists():
            continue
        for p in search_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                meta = d.get("meta", d)
                if meta.get("home_team") == home and meta.get("away_team") == away:
                    candidates.append((p.stat().st_mtime, p))
            except Exception:
                pass
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _find_match_row_in_csv(data_dir: Path, home: str, away: str) -> Optional[dict]:
    """Hľadá odohraný zápas v matches.csv (football-data flat formát)."""
    import csv as _csv_mod
    csv_path = Path(data_dir) / "matches.csv"
    if not csv_path.exists():
        return None
    try:
        with open(csv_path, encoding="utf-8", newline="") as f:
            for row in _csv_mod.DictReader(f):
                if (row.get("home_team") == home and
                        row.get("away_team") == away and
                        row.get("status", "").lower() in ("full_time", "played") and
                        row.get("home_score", "").strip()):
                    return dict(row)
    except Exception:
        pass
    return None


def _read_nested_stat(stats: dict, key: str) -> Optional[float]:
    """Číta štatistiku z nested stats dict.
    Pro League: priama hodnota (int/float).
    Eredivisie: dict s kľúčom 'total'.
    """
    if key not in stats:
        return None
    val = stats[key]
    if isinstance(val, dict):
        v = val.get("total")
        return None if v is None else _to_num(v)
    return 0.0 if val is None else _to_num(val)


def _read_stat_values(d: dict, market: str) -> tuple[Optional[float], Optional[float]]:
    """
    Číta home_val, away_val zo zápasového JSON.
    Formáty:
      - Niké/Chance liga: d["meta"] prítomné, stats v d["stats"]["total"][market]["home/away"]
      - Ligue 1: flat kľúče d["home_fouls"], d["away_fouls"] atď.
      - Pro League: d["home"]["fouls"] = int
      - Eredivisie: d["home"]["fouls"] = {"total": int, ...}
    """
    if "meta" in d:
        # Niké/Chance liga — nested cez meta
        stats_root  = d.get("stats", {})
        total_stats = stats_root.get("total", stats_root)
        stat_key    = MARKET_STAT_KEY.get(market)
        if not stat_key:
            return None, None
        stat = total_stats.get(stat_key, {})
        return _to_num(stat.get("home")), _to_num(stat.get("away"))

    # Ligue 1 — flat kľúče priamo v d
    flat_keys = FLAT_MARKET_STAT_KEY.get(market)
    if flat_keys:
        hk, ak = flat_keys
        if hk in d and ak in d:
            hv = 0.0 if d[hk] is None else _to_num(d[hk])
            av = 0.0 if d[ak] is None else _to_num(d[ak])
            return hv, av

    # Pro League / Eredivisie — nested d["home"][stat] / d["away"][stat]
    home_stats = d.get("home")
    away_stats = d.get("away")
    if isinstance(home_stats, dict) and isinstance(away_stats, dict):
        stat_key = NESTED_MARKET_STAT_KEY.get(market)
        if not stat_key:
            return None, None
        hv = _read_nested_stat(home_stats, stat_key)
        av = _read_nested_stat(away_stats, stat_key)
        if hv is None or av is None:
            return None, None
        return hv, av

    return None, None


def settle_tips(data_dir: Path, github_token: str | None = None) -> tuple[int, int]:
    """
    Vyhodnotí pending tipy podľa výsledkov v data/json/ alebo priamo v data_dir.
    Vracia (vyhodnotené, chyby).
    """
    _migrate_sqlite_if_needed(data_dir)
    tips_csv = Path(data_dir) / TIPS_CSV_NAME
    if not tips_csv.exists():
        return 0, 0

    df = pd.read_csv(tips_csv, dtype=str)

    def _has_complete_stats(d: dict) -> bool:
        if "meta" in d:
            second_half = d.get("stats", {}).get("second_half", {})
            for val in second_half.values():
                if isinstance(val, dict):
                    if any(v not in (None, 0) for v in val.values()):
                        return True
            return False
        # Pre_match / scheduled → explicitne nedokončené
        status = str(d.get("status") or "").lower()
        if "pre" in status or "schedul" in status:
            return False
        hs = d.get("home_score")
        return hs is not None and str(hs).strip() not in ("", "None")

    def _load_match_data(home: str, away: str, mid: str) -> Optional[dict]:
        """Načíta zápasové dáta: JSON (všetky formáty) alebo CSV fallback (football-data)."""
        jp = _find_match_json(data_dir, mid)
        if not jp and mid in ("0", "", "None"):
            jp = _find_match_json_by_teams(data_dir, home, away)
        if jp:
            try:
                return json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                return None
        return _find_match_row_in_csv(data_dir, home, away)

    def _needs_settle(row) -> bool:
        if row["status"] == "pending":
            return True
        d = _load_match_data(str(row["home_team"]), str(row["away_team"]), str(row["match_id"]))
        if d is None:
            return False
        try:
            if not _has_complete_stats(d):
                return False
            # Re-settle 1x2 štatistické tipy, ak actual_value nezodpovedá štatistike
            if row["status"] in ("won", "lost") and str(row.get("bet_type")) == "1x2":
                hv, av = _read_stat_values(d, str(row.get("market", "")))
                if hv is not None and av is not None:
                    return str(row.get("actual_value", "")) != f"{hv}:{av}"
            return False
        except Exception:
            return False

    settle_mask = df.apply(_needs_settle, axis=1)
    if not settle_mask.any():
        return 0, 0

    settled = 0
    errors = 0

    for idx in df[settle_mask].index:
        tip = df.loc[idx]
        mid = str(tip["match_id"])
        d = _load_match_data(str(tip["home_team"]), str(tip["away_team"]), mid)
        if d is None:
            continue

        try:
            # Preskoč zápasy bez výsledku
            if "meta" in d:
                if d.get("meta", {}).get("home_score") is None:
                    continue
            else:
                hs = d.get("home_score")
                if hs is None or str(hs).strip() in ("", "None"):
                    continue

            bet_type  = tip["bet_type"]
            direction = tip["direction"]
            line_str  = tip["line"]
            line      = float(line_str) if line_str else None
            bm_odds   = float(tip["bm_odds"])
            stake     = float(tip["stake"])

            if bet_type in ("total", "home", "away"):
                if line is None:
                    errors += 1
                    continue
                home_val, away_val = _read_stat_values(d, tip["market"])
                if home_val is None or away_val is None:
                    errors += 1
                    continue
                if bet_type == "total":
                    actual = home_val + away_val
                elif bet_type == "home":
                    actual = home_val
                else:
                    actual = away_val
                won = (actual > line) if direction == "O" else (actual < line)
                actual_str = str(actual)

            elif bet_type == "1x2":
                hv, av = _read_stat_values(d, tip["market"])
                if hv is None or av is None:
                    errors += 1
                    continue
                if direction == "1":
                    won = hv > av
                elif direction == "X":
                    won = hv == av
                else:
                    won = av > hv
                actual_str = f"{hv}:{av}"

            else:
                errors += 1
                continue

            profit = round((bm_odds - 1) * stake if won else -stake, 2)
            df.at[idx, "status"]       = "won" if won else "lost"
            df.at[idx, "actual_value"] = actual_str
            df.at[idx, "profit"]       = str(profit)
            settled += 1

        except Exception as e:
            logger.error("Chyba vyhodnotenia tipu %s: %s", tip.get("tip_id"), e)
            errors += 1

    df.to_csv(tips_csv, index=False)

    if github_token and settled > 0:
        try:
            from github_sync import push_tips_csv
            push_tips_csv(data_dir, github_token)
        except Exception as e:
            logger.warning("GitHub sync zlyhala: %s", e)

    return settled, errors


def load_tips(data_dir: Path) -> pd.DataFrame:
    """Načíta tips.csv ako DataFrame. Vracia prázdny DF ak súbor neexistuje."""
    _migrate_sqlite_if_needed(data_dir)
    tips_csv = Path(data_dir) / TIPS_CSV_NAME
    if not tips_csv.exists():
        return pd.DataFrame(columns=TIP_FIELDS)
    return pd.read_csv(tips_csv, dtype=str)


def tips_summary(df: pd.DataFrame) -> dict:
    """Vráti štatistiky: pending/won/lost, profit, ROI."""
    if df.empty:
        return {"pending": 0, "won": 0, "lost": 0, "profit": 0.0, "roi": None}
    pending = int((df["status"] == "pending").sum())
    won     = int((df["status"] == "won").sum())
    lost    = int((df["status"] == "lost").sum())
    settled = df[df["status"].isin(["won", "lost"])].copy()
    profit, roi = 0.0, None
    if not settled.empty:
        profits      = pd.to_numeric(settled["profit"], errors="coerce").fillna(0)
        stakes       = pd.to_numeric(settled["stake"],  errors="coerce").fillna(1)
        profit       = float(profits.sum())
        total_staked = float(stakes.sum())
        roi = profit / total_staked if total_staked > 0 else None
    return {"pending": pending, "won": won, "lost": lost, "profit": profit, "roi": roi}
