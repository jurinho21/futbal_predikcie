"""
nikeliga_model.py — Poisson model na bočné trhy (fauly, SoT, rohy, žlté karty)
s referee-adjustom a walk-forward backtestom.

Použitie:
    python nikeliga_model.py backtest ./data
    python nikeliga_model.py predict ./data --home Slovan --away DAC --referee "Lukáš Dzivjak"
"""

import sys
import json
import argparse
import math
import logging
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
    import numpy as np
    from scipy.stats import poisson, nbinom
    from scipy.special import gammaln
except ImportError:
    logging.critical("Chýbajú knižnice. Spusti: pip install pandas numpy scipy")
    sys.exit(1)

from config import (
    MARKETS, DECAY, CREDIBILITY_K,
    REFEREE_MIN, REFEREE_FULL,
    H2H_WEIGHT, X1X2_SHRINKS, YELLOW_REF_STRENGTH, HR_LINES,
    X1X2_MAX_DRAW_ODDS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NAČÍTANIE DÁT
# ---------------------------------------------------------------------------

def load_data(data_dir: Path) -> pd.DataFrame:
    matches_csv = Path(data_dir) / "matches.csv"
    if not matches_csv.exists():
        logger.critical("Nenájdený %s. Najprv spusti: python nikeliga_batch.py export-csv %s", matches_csv, data_dir)
        sys.exit(1)

    df = pd.read_csv(matches_csv, parse_dates=["date"], dayfirst=True)
    df = df.sort_values("date").reset_index(drop=True)

    # Odvod totálov
    for mkt, cols in MARKETS.items():
        h, a, tot = cols["home_col"], cols["away_col"], cols["total_col"]
        if h in df.columns and a in df.columns:
            df[tot] = pd.to_numeric(df[h], errors="coerce") + pd.to_numeric(df[a], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# ZÁKLADNÉ ŠTATISTICKÉ FUNKCIE
# ---------------------------------------------------------------------------

def _wmean(series: pd.Series, decay: float = DECAY) -> Optional[float]:
    """Exponenciálne vážený priemer — novší zápas má vyššiu váhu."""
    vals = pd.to_numeric(series, errors="coerce").dropna().values
    if len(vals) == 0:
        return None
    n = len(vals)
    weights = np.array([math.exp(-decay * (n - 1 - i)) for i in range(n)])
    return float(np.dot(vals, weights) / weights.sum())


def _hit_rate(series: pd.Series, line: float) -> Optional[float]:
    """Podiel zápasov kde hodnota presiahla líniu."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) < 3:
        return None
    return float((vals > line).mean())


def _team_stats(subset: pd.DataFrame, team: str, side: str,
                commit_col: str, receive_col: str) -> dict:
    """
    Vráti vážené priemery faulov/SoT/rohov pre tím podľa strany (home/away).
    commit_col  = stĺpec kde tím generuje danú štatistiku (napr. home_fouls)
    receive_col = stĺpec kde súper generuje štatistiku proti tomuto tímu
    """
    if side == "home":
        rows = subset[subset["home_team"] == team]
    else:
        rows = subset[subset["away_team"] == team]

    commits = _wmean(rows[commit_col])
    receives = _wmean(rows[receive_col])  # koľko súper generuje PROTI tomuto tímu
    n = len(rows)
    return {"commits": commits, "receives": receives, "n": n}


def _estimate_nb_r(series: pd.Series, min_n: int = 20) -> Optional[float]:
    """Odhadne NB dispersný parameter r (metóda momentov). Ak variance <= mean, vracia None (použije sa Poisson)."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) < min_n:
        return None
    mean = vals.mean()
    var = vals.var()
    if var <= mean:
        return None
    return float(mean ** 2 / (var - mean))


def _h2h_team_stats(subset: pd.DataFrame, home: str, away: str,
                    h_col: str, a_col: str) -> tuple[Optional[float], Optional[float]]:
    """
    Vážený H2H priemer pre každý tím zvlášť (oba smery stretnutí).
    Vracia (h2h_home_val, h2h_away_val) — štatistika aktuálneho domáceho/hosťa
    v ich vzájomných zápasoch bez ohľadu na to, kto vtedy hral doma.
    """
    h2h = subset[
        ((subset["home_team"] == home) & (subset["away_team"] == away)) |
        ((subset["home_team"] == away) & (subset["away_team"] == home))
    ]
    if len(h2h) < 2:
        return None, None

    home_vals, away_vals = [], []
    for _, row in h2h.iterrows():
        hv = pd.to_numeric(row[h_col], errors="coerce")
        av = pd.to_numeric(row[a_col], errors="coerce")
        if pd.isna(hv) or pd.isna(av):
            continue
        if row["home_team"] == home:
            home_vals.append(hv)
            away_vals.append(av)
        else:
            home_vals.append(av)
            away_vals.append(hv)

    if len(home_vals) < 2:
        return None, None
    return _wmean(pd.Series(home_vals)), _wmean(pd.Series(away_vals))


def _ou_hit_rates(series: pd.Series, lines) -> dict:
    """Vráti {line: 'r/n'} — koľkokrát hodnota presiahla líniu."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    n = len(vals)
    if n == 0:
        return {}
    return {line: f"{int((vals > line).sum())}/{n}" for line in lines}


def _merge_hit_rates(hr1: dict, hr2: dict) -> dict:
    """Zlúči dva 'r/n' slovníky sčítaním počtov — pre kombinovaný credibility blend."""
    result = {}
    for line in set(hr1) | set(hr2):
        r, n = 0, 0
        for hr in (hr1, hr2):
            v = hr.get(line, "")
            if v and "/" in str(v):
                ri, ni = map(int, str(v).split("/"))
                r += ri; n += ni
        if n > 0:
            result[line] = f"{r}/{n}"
    return result


def _credibility_blend(model_ou: dict, hit_rates: dict, K: float = CREDIBILITY_K) -> dict:
    """
    Credibility blend: p_blend = (n/(n+K)) * empirical + (K/(n+K)) * p_model
    hit_rates: {line_float: 'r/n'}  — formát z _ou_hit_rates
    Zachová monotónnosť Over probs a O+U=1.
    """
    blended: dict[str, float] = {}
    for key, p_model in model_ou.items():
        if not key.startswith("O"):
            continue
        line = float(key[1:])
        hr = hit_rates.get(line, "")
        if hr and "/" in str(hr):
            r, n = map(int, str(hr).split("/"))
            if n > 0:
                w = n / (n + K)
                p = w * (r / n) + (1 - w) * p_model
                blended[key] = round(max(0.001, min(0.999, p)), 4)
                continue
        blended[key] = p_model

    # Monotónnosť: Over klesá s rastúcou líniou
    for i, k in enumerate(sorted(blended, key=lambda k: float(k[1:]))):
        if i == 0:
            prev_k = k
            continue
        if blended[k] > blended[prev_k]:
            blended[k] = blended[prev_k]
        prev_k = k

    # U = 1 - O
    for k, p_o in list(blended.items()):
        blended[f"U{k[1:]}"] = round(1.0 - p_o, 4)

    return blended


def _credibility_blend_total(model_ou: dict, hr_home: dict, hr_away: dict,
                              K: float = CREDIBILITY_K) -> dict:
    """
    Total blend — priemer blendu domácich zápasov home tímu a hosťujúcich away tímu.
    """
    b1 = _credibility_blend(model_ou, hr_home, K)
    b2 = _credibility_blend(model_ou, hr_away, K)
    combined: dict[str, float] = {}
    for k in b1:
        if k.startswith("O"):
            p = round((b1[k] + b2.get(k, b1[k])) / 2, 4)
            combined[k] = p
            combined[f"U{k[1:]}"] = round(1.0 - p, 4)
    return combined


def _referee_stats(subset: pd.DataFrame, referee: str, tot_col: str,
                   strength: float = 1.0,
                   h_col: Optional[str] = None, a_col: Optional[str] = None,
                   hr_lines: tuple = ()) -> dict:
    """
    Štatistiky rozhodcu: vážený priemer totálov + hit rate nad líniami.
    strength > 1.0 zosilní vplyv rozhodcu (napr. žlté karty = 1.3).
    Ak sú zadané h_col/a_col, vypočíta aj multiplier_home a multiplier_away zvlášť.
    Ak je zadané hr_lines, vypočíta hit_rates_total/home/away pre celý rozsah línií.
    """
    _empty = {"multiplier": 1.0, "multiplier_home": 1.0, "multiplier_away": 1.0,
              "n": 0, "hit_rates": {}, "hit_rates_total": {}, "hit_rates_home": {}, "hit_rates_away": {}}

    if not referee:
        return _empty

    ref_rows = subset[subset["referee"].str.contains(
        referee.split(",")[0].strip(), na=False, case=False
    )]

    n = len(ref_rows)
    if n < REFEREE_MIN:
        return {**_empty, "n": n}

    def _calc_multiplier(series: pd.Series, league_series: pd.Series) -> float:
        league_mean = pd.to_numeric(league_series, errors="coerce").mean()
        if not league_mean:
            return 1.0
        ref_wmean = _wmean(pd.to_numeric(series, errors="coerce").dropna()) or league_mean
        raw = ref_wmean / league_mean
        weight = min(1.0, (n - REFEREE_MIN) / (REFEREE_FULL - REFEREE_MIN))
        return 1.0 + min(weight * strength, 1.0) * (raw - 1.0)

    totals = pd.to_numeric(ref_rows[tot_col], errors="coerce").fillna(0)
    league_mean = pd.to_numeric(subset[tot_col], errors="coerce").mean()
    multiplier = _calc_multiplier(ref_rows[tot_col], subset[tot_col])
    multiplier_home = _calc_multiplier(ref_rows[h_col], subset[h_col]) if h_col else multiplier
    multiplier_away = _calc_multiplier(ref_rows[a_col], subset[a_col]) if a_col else multiplier

    # Starý hit_rates okolo priemeru ligy (spätná kompatibilita)
    if pd.isna(league_mean):
        return {"multiplier": multiplier, "multiplier_home": multiplier_home,
                "multiplier_away": multiplier_away, "n": n, "hit_rates": {},
                "hit_rates_total": {}, "hit_rates_home": {}, "hit_rates_away": {},
                "avg_total": None, "avg_home": None, "avg_away": None,
                "league_avg_total": None, "league_avg_home": None, "league_avg_away": None}
    center = round(league_mean)
    hit_rates = {}
    for offset in range(-3, 4):
        line = center + offset + 0.5
        if line > 0:
            hr = _hit_rate(totals, line)
            if hr is not None:
                hit_rates[line] = hr

    # Rozšírené hit rates pre celý rozsah línií
    hit_rates_total = _ou_hit_rates(totals, hr_lines) if hr_lines else {}
    hit_rates_home = _ou_hit_rates(
        pd.to_numeric(ref_rows[h_col], errors="coerce").fillna(0), hr_lines
    ) if (h_col and hr_lines) else {}
    hit_rates_away = _ou_hit_rates(
        pd.to_numeric(ref_rows[a_col], errors="coerce").fillna(0), hr_lines
    ) if (a_col and hr_lines) else {}

    # Priemery pre zobrazenie v UI
    avg_total = _wmean(totals)
    avg_home  = _wmean(pd.to_numeric(ref_rows[h_col], errors="coerce").dropna()) if h_col else None
    avg_away  = _wmean(pd.to_numeric(ref_rows[a_col], errors="coerce").dropna()) if a_col else None
    league_avg_home = pd.to_numeric(subset[h_col], errors="coerce").mean() if h_col else None
    league_avg_away = pd.to_numeric(subset[a_col], errors="coerce").mean() if a_col else None

    return {"multiplier": multiplier, "multiplier_home": multiplier_home,
            "multiplier_away": multiplier_away, "n": n, "hit_rates": hit_rates,
            "hit_rates_total": hit_rates_total,
            "hit_rates_home": hit_rates_home,
            "hit_rates_away": hit_rates_away,
            "avg_total": round(avg_total, 1) if avg_total else None,
            "avg_home":  round(avg_home,  1) if avg_home  else None,
            "avg_away":  round(avg_away,  1) if avg_away  else None,
            "league_avg_total": round(float(league_mean), 1) if league_mean else None,
            "league_avg_home":  round(float(league_avg_home), 1) if league_avg_home else None,
            "league_avg_away":  round(float(league_avg_away), 1) if league_avg_away else None}


# ---------------------------------------------------------------------------
# PREDIKCIA JEDNÉHO ZÁPASU
# ---------------------------------------------------------------------------


def _cap_draw_odds(probs: dict, max_draw_odds: float) -> dict:
    """Ak P(X) implikuje kurz > max_draw_odds, zvýši P(X) na minimum a prebytok
    proporčne odoberie z P(1) a P(2)."""
    min_pX = 1.0 / max_draw_odds
    pX = probs["X"]
    if pX >= min_pX:
        return probs
    p1, p2 = probs["1"], probs["2"]
    deficit = min_pX - pX
    total_12 = p1 + p2
    if total_12 <= 0:
        return probs
    p1 = max(0.0, p1 - deficit * p1 / total_12)
    p2 = max(0.0, p2 - deficit * p2 / total_12)
    return {"1": round(p1, 4), "X": round(min_pX, 4), "2": round(p2, 4)}


def _winrate_1x2(
    home_rec: Optional[dict],
    away_rec: Optional[dict],
    h2h_rec: Optional[dict] = None,
) -> dict:
    """P(1/X/2) z win-rate: kombinuje domáci win-rate s hosťujúcim loss-rate a naopak.
    H2H zápasy sa počítajú s váhou 1.5x oproti sezónnym zápasov."""
    neutral = {"w": 1, "d": 1, "l": 1, "n": 3}
    hr = home_rec or neutral
    ar = away_rec or neutral
    nh, na = hr["n"], ar["n"]
    if nh == 0 or na == 0:
        return {"1": round(1/3, 4), "X": round(1/3, 4), "2": round(1/3, 4)}

    p1 = (hr["w"] / nh + ar["l"] / na) / 2
    p2 = (ar["w"] / na + hr["l"] / nh) / 2
    pX = (hr["d"] / nh + ar["d"] / na) / 2
    total = p1 + pX + p2
    if total > 0:
        p1, pX, p2 = p1 / total, pX / total, p2 / total

    if h2h_rec and h2h_rec["n"] >= 2:
        n = h2h_rec["n"]
        h2h_p1 = h2h_rec["w"] / n
        h2h_p2 = h2h_rec["l"] / n
        h2h_pX = h2h_rec["d"] / n
        season_n = (nh + na) / 2
        h2h_n_eff = n * 1.5
        w = h2h_n_eff / (season_n + h2h_n_eff)
        p1 = (1 - w) * p1 + w * h2h_p1
        p2 = (1 - w) * p2 + w * h2h_p2
        pX = (1 - w) * pX + w * h2h_pX

    return {"1": round(p1, 4), "X": round(pX, 4), "2": round(p2, 4)}


def _build_1x2_entries(
    market: str, h_col: str, a_col: str,
    league_h: float, league_a: float,
    lambda_home: float, lambda_away: float,
    nb_r_home: Optional[float], nb_r_away: Optional[float],
    subset: "pd.DataFrame", home_team: str, away_team: str,
    commits_home: Optional[float] = None, commits_away: Optional[float] = None,
) -> dict:
    """Vráti {x1x2_key: data} pre daný market, ostatné kľúče = None.
    Žlté karty: Poisson/NB cez lambda. Ostatné: win-rate kombinácia."""
    from config import X1X2_KEYS
    key = X1X2_KEYS[market]

    home_rows = subset[subset["home_team"] == home_team]
    away_rows = subset[subset["away_team"] == away_team]
    raw_home_commits  = _wmean(home_rows[h_col]) or league_h
    raw_home_receives = _wmean(home_rows[a_col]) or league_a
    raw_away_commits  = _wmean(away_rows[a_col]) or league_a
    raw_away_receives = _wmean(away_rows[h_col]) or league_h

    home_rec = _win_record(subset, home_team, "home", h_col, a_col)
    away_rec = _win_record(subset, away_team, "away", h_col, a_col)

    h2h_rec = _h2h_win_record(subset, home_team, away_team, h_col, a_col)
    model_probs = _winrate_1x2(home_rec, away_rec, h2h_rec)
    model_probs = _cap_draw_odds(model_probs, X1X2_MAX_DRAW_ODDS[market])

    data = {
        **model_probs,
        "home_record":   home_rec,
        "away_record":   away_rec,
        "home_commits":  round(raw_home_commits,  1),
        "home_receives": round(raw_home_receives, 1),
        "away_commits":  round(raw_away_commits,  1),
        "away_receives": round(raw_away_receives, 1),
    }
    # Zachovaj None pre ostatné 1x2 kľúče (backward compat s app.py)
    all_keys = list(X1X2_KEYS.values())
    return {k: (data if k == key else None) for k in all_keys}


def _compute_market_lambdas(
    subset: pd.DataFrame,
    home_team: str, away_team: str,
    h_col: str, a_col: str,
    market: str,
    mot_home: float, mot_away: float,
) -> tuple:
    """Vráti (lambda_home, lambda_away, league_h, league_a, home_n, away_n, h2h_home, h2h_away, commits_home, commits_away).
    commits_home/away = iba vlastné fauly tímu (bez receives) — použité pre 1X2."""
    league_h = pd.to_numeric(subset[h_col], errors="coerce").mean() or 1.0
    league_a = pd.to_numeric(subset[a_col], errors="coerce").mean() or 1.0

    home_s = _team_stats(subset, home_team, "home", h_col, a_col)
    away_s = _team_stats(subset, away_team, "away", a_col, h_col)

    home_commits = home_s["commits"] or league_h
    away_commits = away_s["commits"] or league_a
    home_receives = home_s["receives"] or league_a
    away_receives = away_s["receives"] or league_h

    lambda_home = (home_commits + away_receives) / 2
    lambda_away = (away_commits + home_receives) / 2

    # commits pre 1X2 (blendované s H2H zvlášť od averaged lambda)
    commits_home_1x2 = home_commits
    commits_away_1x2 = away_commits

    h2h_home, h2h_away = _h2h_team_stats(subset, home_team, away_team, h_col, a_col)
    if h2h_home is not None:
        lambda_home = (1 - H2H_WEIGHT) * lambda_home + H2H_WEIGHT * h2h_home
        lambda_away = (1 - H2H_WEIGHT) * lambda_away + H2H_WEIGHT * h2h_away
        # pre 1X2 neupravujeme commits cez H2H priemer — H2H sa zahrnie cez win record

    if market in ("fouls", "yellow_cards"):
        lambda_home *= mot_home
        lambda_away *= mot_away
        commits_home_1x2 *= mot_home
        commits_away_1x2 *= mot_away

    return lambda_home, lambda_away, league_h, league_a, home_s["n"], away_s["n"], h2h_home, h2h_away, commits_home_1x2, commits_away_1x2


def _apply_referee(
    subset: pd.DataFrame, referee: Optional[str],
    market: str, tot_col: str, h_col: str, a_col: str,
    lambda_home: float, lambda_away: float,
) -> tuple:
    """Aplikuje referee adjust. Vráti (lambda_home, lambda_away, ref_info)."""
    if market == "fouls":
        ref_info = _referee_stats(subset, referee or "", tot_col, strength=1.0,
                                  h_col=h_col, a_col=a_col, hr_lines=HR_LINES)
    elif market == "yellow_cards":
        ref_info = _referee_stats(subset, referee or "", tot_col,
                                  strength=YELLOW_REF_STRENGTH,
                                  h_col=h_col, a_col=a_col, hr_lines=HR_LINES)
    else:
        ref_info = {"multiplier": 1.0, "multiplier_home": 1.0, "multiplier_away": 1.0,
                    "n": 0, "hit_rates": {}}

    lambda_home *= ref_info["multiplier_home"]
    lambda_away *= ref_info["multiplier_away"]
    return lambda_home, lambda_away, ref_info


def _compute_nb_params(
    subset: pd.DataFrame, home_team: str, away_team: str,
    h_col: str, a_col: str, tot_col: str,
) -> tuple:
    """Vráti (nb_r, nb_r_home, nb_r_away) — Negative Binomial dispersion parametre."""
    nb_r = _estimate_nb_r(subset[tot_col])
    home_series = subset[subset["home_team"] == home_team][h_col]
    away_series = subset[subset["away_team"] == away_team][a_col]
    nb_r_home = _estimate_nb_r(home_series, min_n=8) or _estimate_nb_r(subset[h_col])
    nb_r_away = _estimate_nb_r(away_series, min_n=8) or _estimate_nb_r(subset[a_col])
    return nb_r, nb_r_home, nb_r_away


def _compute_market_hit_rates(
    home_rows: pd.DataFrame, away_rows: pd.DataFrame, h2h_rows: pd.DataFrame,
    h_col: str, a_col: str,
    home_all_rows: Optional[pd.DataFrame] = None,
    away_all_rows: Optional[pd.DataFrame] = None,
) -> dict:
    """Vráti slovník hit rate diktov pre totál, tímové a H2H štatistiky."""
    def _total(rows):
        h = pd.to_numeric(rows[h_col], errors="coerce")
        a = pd.to_numeric(rows[a_col], errors="coerce")
        return h + a  # NaN + NaN = NaN → _ou_hit_rates ich správne vynechá cez .dropna()

    result = {
        "hit_rates_home_total":    _ou_hit_rates(_total(home_rows), HR_LINES),
        "hit_rates_away_total":    _ou_hit_rates(_total(away_rows), HR_LINES),
        "hit_rates_h2h_total":     _ou_hit_rates(_total(h2h_rows),  HR_LINES),
        "hit_rates_home_stat":     _ou_hit_rates(pd.to_numeric(home_rows[h_col], errors="coerce").fillna(0), HR_LINES),
        "hit_rates_away_stat":     _ou_hit_rates(pd.to_numeric(away_rows[a_col], errors="coerce").fillna(0), HR_LINES),
        "hit_rates_home_opp_stat": _ou_hit_rates(pd.to_numeric(home_rows[a_col], errors="coerce").fillna(0), HR_LINES),
        "hit_rates_away_opp_stat": _ou_hit_rates(pd.to_numeric(away_rows[h_col], errors="coerce").fillna(0), HR_LINES),
        "hit_rates_home_all":      _ou_hit_rates(_total(home_all_rows), HR_LINES) if home_all_rows is not None else {},
        "hit_rates_away_all":      _ou_hit_rates(_total(away_all_rows), HR_LINES) if away_all_rows is not None else {},
    }
    return result


def _h2h_match_records(h2h_rows: pd.DataFrame, h_col: str, a_col: str) -> list:
    if h2h_rows.empty:
        return []
    cols = [c for c in ["match_id", "date", "home_team", "away_team", h_col, a_col] if c in h2h_rows.columns]
    stat_cols = [c for c in [h_col, a_col] if c in h2h_rows.columns]
    df = h2h_rows[cols].copy()
    for c in stat_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df.sort_values("match_id", ascending=False).head(5).to_dict("records")


def _predict_market(
    market: str, cols: dict,
    subset: pd.DataFrame, home_team: str, away_team: str,
    referee: Optional[str], mot_home: float, mot_away: float,
    home_rows: pd.DataFrame, away_rows: pd.DataFrame, h2h_rows: pd.DataFrame,
) -> dict:
    """Predikuje jeden market. Volané z predict_match() pre každý market zvlášť."""
    h_col, a_col, tot_col = cols["home_col"], cols["away_col"], cols["total_col"]

    lambda_home, lambda_away, league_h, league_a, home_n, away_n, h2h_home, h2h_away, commits_home_1x2, commits_away_1x2 = (
        _compute_market_lambdas(subset, home_team, away_team, h_col, a_col, market, mot_home, mot_away)
    )
    lambda_home, lambda_away, ref_info = _apply_referee(
        subset, referee, market, tot_col, h_col, a_col, lambda_home, lambda_away
    )
    commits_home_1x2 *= ref_info["multiplier_home"]
    commits_away_1x2 *= ref_info["multiplier_away"]
    lambda_total = lambda_home + lambda_away

    nb_r, nb_r_home, nb_r_away = _compute_nb_params(subset, home_team, away_team, h_col, a_col, tot_col)

    ou       = _ou_probabilities(lambda_total, r=nb_r)
    ou_home  = _ou_probabilities(lambda_home,  r=nb_r_home)
    ou_away  = _ou_probabilities(lambda_away,  r=nb_r_away)

    # Všetky zápasy tímu (ako domáci aj ako hosť) — pre stĺpce "cel."
    home_all_rows = subset[(subset["home_team"] == home_team) | (subset["away_team"] == home_team)]
    away_all_rows = subset[(subset["home_team"] == away_team) | (subset["away_team"] == away_team)]
    hit_rates = _compute_market_hit_rates(
        home_rows, away_rows, h2h_rows, h_col, a_col, home_all_rows, away_all_rows
    )

    result = {
        "lambda_home":  round(lambda_home, 2),
        "lambda_away":  round(lambda_away, 2),
        "lambda_total": round(lambda_total, 2),
        "over_under":      ou,
        "over_under_home": ou_home,
        "over_under_away": ou_away,
        "nb_r":      round(nb_r, 2)      if nb_r      else None,
        "nb_r_home": round(nb_r_home, 2) if nb_r_home else None,
        "nb_r_away": round(nb_r_away, 2) if nb_r_away else None,
        **_build_1x2_entries(market, h_col, a_col, league_h, league_a,
                             lambda_home, lambda_away, nb_r_home, nb_r_away,
                             subset, home_team, away_team,
                             commits_home=commits_home_1x2, commits_away=commits_away_1x2),
        "ref_info": ref_info,
        "home_n":   home_n,
        "away_n":   away_n,
        "h2h_avg":     round(h2h_home + h2h_away, 1) if h2h_home is not None else None,
        "h2h_matches": _h2h_match_records(h2h_rows, h_col, a_col),
        **hit_rates,
    }

    result["over_under_blended"] = _credibility_blend_total(
        ou, hit_rates["hit_rates_home_total"], hit_rates["hit_rates_away_total"]
    )
    result["over_under_home_blended"] = _credibility_blend(
        ou_home,
        _merge_hit_rates(hit_rates["hit_rates_home_stat"], hit_rates["hit_rates_away_opp_stat"]),
    )
    result["over_under_away_blended"] = _credibility_blend(
        ou_away,
        _merge_hit_rates(hit_rates["hit_rates_away_stat"], hit_rates["hit_rates_home_opp_stat"]),
    )

    return result


def predict_match(
    df: pd.DataFrame,
    home_team: str,
    away_team: str,
    referee: Optional[str] = None,
    before_idx: Optional[int] = None,
    mot_home: float = 1.0,
    mot_away: float = 1.0,
) -> dict:
    if before_idx is None:
        before_idx = len(df)

    subset    = df.iloc[:before_idx]
    home_rows = subset[subset["home_team"] == home_team]
    away_rows = subset[subset["away_team"] == away_team]
    h2h_rows  = subset[
        ((subset["home_team"] == home_team) & (subset["away_team"] == away_team)) |
        ((subset["home_team"] == away_team) & (subset["away_team"] == home_team))
    ]

    return {
        market: _predict_market(
            market, cols, subset, home_team, away_team,
            referee, mot_home, mot_away,
            home_rows, away_rows, h2h_rows,
        )
        for market, cols in MARKETS.items()
    }


def _win_record(subset: pd.DataFrame, team: str, side: str,
                h_col: str, a_col: str) -> Optional[dict]:
    """Historický záznam koľkokrát tím mal viac/rovnako/menej danej štatistiky ako súper."""
    if side == "home":
        rows = subset[subset["home_team"] == team]
        team_fouls = pd.to_numeric(rows[h_col], errors="coerce")
        opp_fouls = pd.to_numeric(rows[a_col], errors="coerce")
    else:
        rows = subset[subset["away_team"] == team]
        team_fouls = pd.to_numeric(rows[a_col], errors="coerce")
        opp_fouls = pd.to_numeric(rows[h_col], errors="coerce")

    valid = team_fouls.notna() & opp_fouls.notna()
    if valid.sum() < 3:
        return None
    tf, of = team_fouls[valid], opp_fouls[valid]
    return {
        "w": int((tf > of).sum()),
        "d": int((tf == of).sum()),
        "l": int((tf < of).sum()),
        "n": int(valid.sum()),
    }


def _h2h_win_record(subset: pd.DataFrame, home_team: str, away_team: str,
                    h_col: str, a_col: str) -> Optional[dict]:
    """W/D/L pre home_team vo vzájomných zápasoch s away_team (oba smery)."""
    h2h = subset[
        ((subset["home_team"] == home_team) & (subset["away_team"] == away_team)) |
        ((subset["home_team"] == away_team) & (subset["away_team"] == home_team))
    ]
    if len(h2h) < 2:
        return None

    hv = pd.to_numeric(h2h[h_col], errors="coerce")
    av = pd.to_numeric(h2h[a_col], errors="coerce")
    valid_mask = hv.notna() & av.notna()
    if valid_mask.sum() < 2:
        return None

    is_home = h2h["home_team"] == home_team
    team_val = hv.where(is_home, av)
    opp_val  = av.where(is_home, hv)

    team_val = team_val[valid_mask]
    opp_val  = opp_val[valid_mask]

    return {
        "w": int((team_val > opp_val).sum()),
        "d": int((team_val == opp_val).sum()),
        "l": int((team_val < opp_val).sum()),
        "n": int(valid_mask.sum()),
    }


def winner_probs(lambda_home: float, lambda_away: float,
                      r_home: Optional[float] = None, r_away: Optional[float] = None,
                      max_k: int = 60) -> dict:
    """P(domáci viac faulov / rovnako / hostia viac faulov) cez NB alebo Poisson."""
    def _pmf(k, lam, r):
        if r is not None:
            return float(nbinom.pmf(k, r, r / (r + lam)))
        return float(poisson.pmf(k, lam))

    def _cdf(k, lam, r):
        if r is not None:
            return float(nbinom.cdf(k, r, r / (r + lam)))
        return float(poisson.cdf(k, lam))

    p_home_more = 0.0
    p_equal = 0.0

    for k in range(max_k + 1):
        ph = _pmf(k, lambda_home, r_home)
        if ph < 1e-10:
            continue
        p_away_lt_k = _cdf(k - 1, lambda_away, r_away) if k > 0 else 0.0
        p_away_eq_k = _pmf(k, lambda_away, r_away)
        p_home_more += ph * p_away_lt_k
        p_equal += ph * p_away_eq_k

    p_away_more = max(0.0, 1.0 - p_home_more - p_equal)
    return {
        "1": round(p_home_more, 4),
        "X": round(p_equal, 4),
        "2": round(p_away_more, 4),
    }


def _ou_probabilities(lam: float, lines: tuple = HR_LINES,
                      r: Optional[float] = None) -> dict:
    """Pravdepodobnosti Over/Under. Ak je r zadané, použije Negative Binomial, inak Poisson."""
    ou = {}
    for line in lines:
        k = int(line + 0.5)
        if r is not None:
            p_nb = r / (r + lam)
            p_under = nbinom.cdf(k - 1, r, p_nb)
        else:
            p_under = poisson.cdf(k - 1, lam)
        ou[f"O{line}"] = round(1 - p_under, 4)
        ou[f"U{line}"] = round(p_under, 4)
    return ou


def fair_odds(p: float) -> float:
    """Konvertuje pravdepodobnosť na fair decimal kurz."""
    return round(1 / p, 2) if p > 0 else 999.0



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Niké liga Poisson model")
    sub = parser.add_subparsers(dest="cmd")

    p_pred = sub.add_parser("predict", help="Predikcia zápasu")
    p_pred.add_argument("data_dir")
    p_pred.add_argument("--home", required=True)
    p_pred.add_argument("--away", required=True)
    p_pred.add_argument("--referee", default="")

    p_up = sub.add_parser("upcoming", help="Fair kurzy pre nadchadzajuce zapasy")
    p_up.add_argument("data_dir")
    p_up.add_argument("--lines", type=int, default=3,
                      help="Pocet linii okolo expected value (default 3)")

    args = parser.parse_args()

    if args.cmd == "predict":
        df = load_data(Path(args.data_dir))
        pred = predict_match(df, args.home, args.away, args.referee)

        print(f"\n=== PREDIKCIA: {args.home} vs {args.away} ===")
        if args.referee:
            print(f"Rozhodca: {args.referee}")
        print()

        for market, data in pred.items():
            print(f"[{market.upper()}]")
            print(f"  Očakávaný total: {data['lambda_total']} "
                  f"(dom {data['lambda_home']} + host {data['lambda_away']})")
            lam = data["lambda_total"]
            # Ukáž 3 najbližšie línie
            for offset in (-1, 0, 1):
                line = round(lam) + offset - 0.5
                if line < 0.5:
                    continue
                p_over = data["over_under"].get(f"O{line}", None)
                p_under = data["over_under"].get(f"U{line}", None)
                if p_over is not None:
                    print(f"  O{line}: {p_over:.1%}  (kurz {fair_odds(p_over)})  |  "
                          f"U{line}: {p_under:.1%}  (kurz {fair_odds(p_under)})")
            print()
    elif args.cmd == "upcoming":
        print_upcoming(Path(args.data_dir), n_lines=args.lines)

    else:
        parser.print_help()


def _upcoming_matches(data_dir: Path) -> list[dict]:
    """Vrati nadchadzajuce zapasy z matches_index.json (bez skore v JSON)."""
    index_path = data_dir / "matches_index.json"
    json_dir = data_dir / "json"
    upcoming = []
    if not index_path.exists():
        return upcoming
    with open(index_path, encoding="utf-8") as f:
        matches = json.load(f)
    for m in matches:
        json_path = json_dir / f"{m['id']}.json"
        if not json_path.exists():
            # Nie je stiahnuty = este neodohrano, ale nemame meta
            upcoming.append({"home": "?", "away": "?", "date": m.get("date", ""), "referee": "", "url": m.get("url", "")})
            continue
        with open(json_path, encoding="utf-8") as f:
            d = json.load(f)
        meta = d.get("meta", {})
        if meta.get("home_score") is None:
            upcoming.append({
                "home": meta.get("home_team", "?"),
                "away": meta.get("away_team", "?"),
                "date": meta.get("date", m.get("date", "")),
                "referee": meta.get("referee", ""),
                "url": m.get("url", ""),
            })
    return upcoming


def print_upcoming(data_dir: Path, n_lines: int = 3):
    df = load_data(data_dir)
    upcoming = _upcoming_matches(data_dir)

    if not upcoming:
        print("Ziadne nadchadzajuce zapasy v indexe. Spusti najprv fetch.")
        return

    print(f"\n{'='*65}")
    print(f"  FAIR KURZY - NADCHADZAJUCE ZAPASY  (model: Poisson exp-decay={DECAY})")
    print(f"{'='*65}")

    market_labels = {
        "fouls": "FAULY",
        "shots_on_target": "STRELY NA BRANU",
        "corners": "ROHOVE KOPY",
        "yellow_cards": "ZLTE KARTY",
    }

    for match in upcoming:
        home, away = match["home"], match["away"]
        date = match["date"]
        referee = match["referee"]

        print(f"\n  {home} vs {away}")
        if date:
            print(f"  {date}")
        if referee:
            print(f"  Rozhodca: {referee}")
        print(f"  {'-'*55}")

        if home == "?" or away == "?":
            print("  (chyba meta data)")
            continue

        try:
            pred = predict_match(df, home, away, referee)
        except Exception as e:
            print(f"  Chyba predikcie: {e}")
            continue

        for market, label in market_labels.items():
            data = pred[market]
            lam = data["lambda_total"]
            note = "  (bez referee adjustu)" if market == "fouls" and not referee else ""
            print(f"\n  [{label}]  expected={lam}{note}")

            # n_lines linii pod a nad EV
            center = lam
            candidates = []
            step = 1.0
            for k in range(-n_lines, n_lines + 1):
                line = round(center) + k - 0.5
                if line < 0.5:
                    continue
                key_o = f"O{line}"
                key_u = f"U{line}"
                p_o = data["over_under"].get(key_o)
                p_u = data["over_under"].get(key_u)
                if p_o is not None:
                    candidates.append((line, p_o, p_u))

            for line, p_o, p_u in candidates:
                ko = fair_odds(p_o)
                ku = fair_odds(p_u)
                # Zvyrazni linky kde je model najviac presvedceny (p daleko od 0.5)
                confidence = abs(p_o - 0.5)
                marker = " <--" if confidence > 0.12 else ""
                print(f"    O{line:<5} {p_o:5.1%}  fair {ko:5.2f}  |  U{line:<5} {p_u:5.1%}  fair {ku:5.2f}{marker}")

    print(f"\n{'='*65}")
    print("  Ako pouzit: porovnaj fair kurz s kurzom bookmakera.")
    print("  Edge = (tvoja_pravdepodobnost * kurz_bookmakera) - 1")
    print("  Stavkuj len ak edge > 0 (kurz bookmakera > fair kurz).")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
