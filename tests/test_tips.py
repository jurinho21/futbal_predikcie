"""
test_tips.py — unit testy pre nikeliga_tips (CSV backend)
"""
import json
import pytest
from pathlib import Path

from nikeliga_tips import save_tips, settle_tips, load_tips, tips_summary, _tip_id


def _make_tip(**overrides) -> dict:
    base = {
        "match_id":   5001,
        "home_team":  "Slovan",
        "away_team":  "DAC",
        "match_date": "2026-05-10 18:00",
        "market":     "fouls",
        "bet_type":   "total",
        "line":       21.5,
        "direction":  "O",
        "model_prob": 0.62,
        "bm_odds":    1.75,
        "edge":       0.085,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _tip_id
# ---------------------------------------------------------------------------

class TestTipId:
    def test_same_inputs_same_id(self):
        a = _tip_id(1, "fouls", "total", 21.5, "O")
        b = _tip_id(1, "fouls", "total", 21.5, "O")
        assert a == b

    def test_different_direction_different_id(self):
        a = _tip_id(1, "fouls", "total", 21.5, "O")
        b = _tip_id(1, "fouls", "total", 21.5, "U")
        assert a != b

    def test_different_market_different_id(self):
        assert _tip_id(1, "fouls", "total", 21.5, "O") != _tip_id(1, "corners", "total", 21.5, "O")


# ---------------------------------------------------------------------------
# save_tips
# ---------------------------------------------------------------------------

class TestSaveTips:
    def test_saves_new_tip(self, tmp_data_dir):
        n = save_tips(tmp_data_dir, [_make_tip()])
        assert n == 1

    def test_deduplicates_same_tip(self, tmp_data_dir):
        save_tips(tmp_data_dir, [_make_tip()])
        n = save_tips(tmp_data_dir, [_make_tip()])
        assert n == 0, "Duplikát nesmie byť uložený"

    def test_saves_multiple_distinct_tips(self, tmp_data_dir):
        tips = [
            _make_tip(direction="O", line=20.5),
            _make_tip(direction="O", line=21.5),
            _make_tip(direction="U", line=22.5),
        ]
        n = save_tips(tmp_data_dir, tips)
        assert n == 3

    def test_saved_tip_has_correct_fields(self, tmp_data_dir):
        save_tips(tmp_data_dir, [_make_tip()])
        df = load_tips(tmp_data_dir)
        row = df.iloc[0]
        assert row["home_team"] == "Slovan"
        assert row["market"] == "fouls"
        assert row["status"] == "pending"

    def test_empty_list_returns_zero(self, tmp_data_dir):
        assert save_tips(tmp_data_dir, []) == 0


# ---------------------------------------------------------------------------
# settle_tips
# ---------------------------------------------------------------------------

class TestSettleTips:
    def _write_match_json(self, data_dir: Path, match_id: int, home_fouls: int, away_fouls: int):
        json_dir = data_dir / "json"
        json_dir.mkdir(exist_ok=True)
        data = {
            "meta": {"home_score": 2, "away_score": 1},
            "stats": {
                "total": {
                    "fouls": {"home": home_fouls, "away": away_fouls},
                }
            },
        }
        (json_dir / f"{match_id}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_over_bet_won(self, tmp_data_dir):
        self._write_match_json(tmp_data_dir, 5001, home_fouls=13, away_fouls=11)
        save_tips(tmp_data_dir, [_make_tip(line=21.5, direction="O")])
        settled, errors = settle_tips(tmp_data_dir)
        assert settled == 1
        assert errors == 0
        df = load_tips(tmp_data_dir)
        assert df.iloc[0]["status"] == "won"

    def test_over_bet_lost(self, tmp_data_dir):
        self._write_match_json(tmp_data_dir, 5001, home_fouls=10, away_fouls=9)
        save_tips(tmp_data_dir, [_make_tip(line=21.5, direction="O")])
        settle_tips(tmp_data_dir)
        df = load_tips(tmp_data_dir)
        assert df.iloc[0]["status"] == "lost"

    def test_under_bet_won(self, tmp_data_dir):
        self._write_match_json(tmp_data_dir, 5001, home_fouls=8, away_fouls=7)
        save_tips(tmp_data_dir, [_make_tip(line=21.5, direction="U")])
        settle_tips(tmp_data_dir)
        df = load_tips(tmp_data_dir)
        assert df.iloc[0]["status"] == "won"

    def test_profit_calculated_correctly(self, tmp_data_dir):
        self._write_match_json(tmp_data_dir, 5001, home_fouls=13, away_fouls=11)
        save_tips(tmp_data_dir, [_make_tip(line=21.5, direction="O", bm_odds=1.75)])
        settle_tips(tmp_data_dir)
        df = load_tips(tmp_data_dir)
        profit = float(df.iloc[0]["profit"])
        assert profit == pytest.approx(0.75, abs=0.01)

    def test_skips_unplayed_matches(self, tmp_data_dir):
        json_dir = tmp_data_dir / "json"
        json_dir.mkdir(exist_ok=True)
        (json_dir / "5001.json").write_text(
            json.dumps({"meta": {"home_score": None}, "stats": {}}),
            encoding="utf-8",
        )
        save_tips(tmp_data_dir, [_make_tip()])
        settled, _ = settle_tips(tmp_data_dir)
        assert settled == 0

    def test_no_tips_returns_zero(self, tmp_data_dir):
        settled, errors = settle_tips(tmp_data_dir)
        assert settled == 0 and errors == 0

    def test_1x2_home_win(self, tmp_data_dir):
        self._write_match_json(tmp_data_dir, 5001, home_fouls=14, away_fouls=10)
        save_tips(tmp_data_dir, [_make_tip(bet_type="1x2", direction="1", line=0.0)])
        settle_tips(tmp_data_dir)
        df = load_tips(tmp_data_dir)
        assert df.iloc[0]["status"] == "won"


# ---------------------------------------------------------------------------
# load_tips + tips_summary
# ---------------------------------------------------------------------------

class TestLoadTips:
    def test_returns_empty_df_when_no_db(self, tmp_data_dir):
        df = load_tips(tmp_data_dir)
        assert df.empty

    def test_returns_correct_columns(self, tmp_data_dir):
        save_tips(tmp_data_dir, [_make_tip()])
        df = load_tips(tmp_data_dir)
        assert "tip_id" in df.columns
        assert "status" in df.columns
        assert "market" in df.columns


class TestTipsSummary:
    def test_empty_df_returns_defaults(self):
        import pandas as pd
        summary = tips_summary(pd.DataFrame())
        assert summary["pending"] == 0
        assert summary["roi"] is None

    def test_pending_count(self, tmp_data_dir):
        save_tips(tmp_data_dir, [_make_tip(line=20.5), _make_tip(line=21.5)])
        df = load_tips(tmp_data_dir)
        s = tips_summary(df)
        assert s["pending"] == 2

    def test_roi_calculated_after_settlement(self, tmp_data_dir):
        json_dir = tmp_data_dir / "json"
        json_dir.mkdir(exist_ok=True)
        (json_dir / "5001.json").write_text(
            json.dumps({
                "meta": {"home_score": 2, "away_score": 1},
                "stats": {"total": {"fouls": {"home": 13, "away": 11}}},
            }),
            encoding="utf-8",
        )
        save_tips(tmp_data_dir, [_make_tip(bm_odds=2.0)])
        settle_tips(tmp_data_dir)
        df = load_tips(tmp_data_dir)
        s = tips_summary(df)
        assert s["roi"] is not None
        assert s["profit"] == pytest.approx(1.0, abs=0.01)
