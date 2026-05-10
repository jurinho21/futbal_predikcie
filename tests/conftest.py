"""
conftest.py — zdieľané fixtures pre všetky testy
"""
import sys
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Pridaj root projektu do sys.path, aby importy fungovali aj mimo venv
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import MARKETS


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """
    Minimálny DataFrame s 60 záznamnými pre 5 tímov.
    Postačuje na testovanie predict_match bez reálnych dát.
    """
    rng = np.random.default_rng(42)
    teams = ["Slovan", "Sparta", "DAC", "Ruzomberok", "Zilina"]
    refs  = ["Dzivjak", "Kráľovič", "Žák"]
    n = 60

    rows = []
    for i in range(n):
        home = teams[i % len(teams)]
        away = teams[(i + 2) % len(teams)]
        rows.append({
            "date":        pd.Timestamp("2024-08-01") + pd.Timedelta(weeks=i),
            "home_team":   home,
            "away_team":   away,
            "referee":     refs[i % len(refs)],
            "home_fouls":          int(rng.integers(8, 20)),
            "away_fouls":          int(rng.integers(8, 20)),
            "home_shots_on_target": int(rng.integers(1, 8)),
            "away_shots_on_target": int(rng.integers(1, 8)),
            "home_corners":        int(rng.integers(2, 10)),
            "away_corners":        int(rng.integers(2, 10)),
            "home_yellow":         int(rng.integers(0, 4)),
            "away_yellow":         int(rng.integers(0, 4)),
        })

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    for mkt, cols in MARKETS.items():
        h, a, tot = cols["home_col"], cols["away_col"], cols["total_col"]
        df[tot] = df[h] + df[a]
    return df


@pytest.fixture
def tmp_data_dir(tmp_path) -> Path:
    """Dočasný adresár simulujúci data/ s json/ podadresárom."""
    (tmp_path / "json").mkdir()
    return tmp_path


@pytest.fixture
def minimal_match_json(tmp_data_dir) -> tuple[Path, dict]:
    """Uloží minimálny JSON zápasu a vráti (json_dir, match_data)."""
    data = {
        "match_id": 1001,
        "season": 2025,
        "meta": {
            "home_team": "Slovan",
            "away_team": "DAC",
            "home_score": 2,
            "away_score": 1,
            "referee": "Dzivjak",
        },
        "stats": {
            "total": {
                "fouls":          {"home": 14, "away": 12},
                "shots_on_target": {"home": 5,  "away": 3},
                "corners":        {"home": 6,  "away": 4},
                "yellow_cards":   {"home": 2,  "away": 1},
            }
        },
    }
    json_path = tmp_data_dir / "json" / "1001.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    return tmp_data_dir, data
