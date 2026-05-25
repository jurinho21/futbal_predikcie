"""Kešované predikčné funkcie zdieľané medzi app a UI widgetmi."""

import os
import pandas as pd
import streamlit as st
from pathlib import Path

from nikeliga_model import predict_match
from config import MARKETS, X1X2_KEYS

_MODEL_MTIME = max(
    os.path.getmtime(Path(__file__).parent / "nikeliga_model.py"),
    os.path.getmtime(Path(__file__).parent / "config.py"),
    os.path.getmtime(Path(__file__).parent / "nikeliga_app.py"),
)

MOT_OPTIONS = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3]


@st.cache_data
def cached_predict(_df, home, away, referee, mot_home=1.0, mot_away=1.0,
                   model_v=_MODEL_MTIME, data_v: float = 0.0, league: str = ""):
    """_df nie je hashovaný — model_v+data_v+league sú cache kľúče.
    data_v = mtime matches.csv → invaliduje keš pri každom novom scrape."""
    return predict_match(_df, home, away, referee or None, mot_home=mot_home, mot_away=mot_away)


@st.cache_data
def run_backtest(_df, min_matches: int = 30, league: str = "") -> pd.DataFrame:
    """Walk-forward backtest: pre každý historický zápas predikuje pomocou len predchádzajúcich dát."""
    rows = []
    n = len(_df)
    for i in range(min_matches, n):
        row = _df.iloc[i]
        home = str(row.get("home_team") or "")
        away = str(row.get("away_team") or "")
        ref = str(row.get("referee") or "")
        if not home or not away:
            continue
        try:
            pred = predict_match(_df, home, away, ref or None, before_idx=i)
        except Exception:
            continue
        for market, cols in MARKETS.items():
            actual = pd.to_numeric(row.get(cols["total_col"]), errors="coerce")
            if pd.isna(actual):
                continue
            lam = pred[market]["lambda_total"]
            ou = pred[market].get("over_under_blended") or pred[market]["over_under"]
            for ou_key, p in ou.items():
                if not ou_key.startswith("O"):
                    continue
                line = float(ou_key[1:])
                if abs(line - lam) > 4:
                    continue
                rows.append({"type": "ou", "market": market, "p_over": float(p), "actual_over": int(actual > line)})
            x1x2_key = X1X2_KEYS.get(market)
            if x1x2_key:
                actual_home = pd.to_numeric(row.get(cols["home_col"]), errors="coerce")
                actual_away = pd.to_numeric(row.get(cols["away_col"]), errors="coerce")
                if not pd.isna(actual_home) and not pd.isna(actual_away):
                    actual_1x2 = "1" if actual_home > actual_away else ("X" if actual_home == actual_away else "2")
                    x1x2_data = pred[market].get(x1x2_key, {})
                    b = x1x2_data.get("blended") or x1x2_data
                    p1, px, p2 = b.get("1"), b.get("X"), b.get("2")
                    if p1 and px and p2:
                        rows.append({
                            "type": "1x2", "market": market,
                            "p1": float(p1), "pX": float(px), "p2": float(p2),
                            "actual_1x2": actual_1x2,
                        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()
