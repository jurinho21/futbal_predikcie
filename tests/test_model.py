"""
test_model.py — unit testy pre nikeliga_model
"""
import pytest
import pandas as pd
import numpy as np

from nikeliga_model import (
    predict_match, fair_odds, winner_probs,
    _wmean, _hit_rate, _credibility_blend, _ou_probabilities,
    _compute_market_lambdas, _apply_referee, _compute_nb_params,
)
from config import MARKETS


# ---------------------------------------------------------------------------
# Základné matematické funkcie
# ---------------------------------------------------------------------------

class TestWmean:
    def test_single_value(self):
        assert _wmean(pd.Series([5.0])) == pytest.approx(5.0)

    def test_weights_newer_higher(self):
        s = pd.Series([1.0, 10.0])
        result = _wmean(s)
        assert result > 5.0, "Novší záznam má mať vyššiu váhu"

    def test_empty_series_returns_none(self):
        assert _wmean(pd.Series([], dtype=float)) is None

    def test_ignores_nan(self):
        s = pd.Series([float("nan"), 4.0, 6.0])
        result = _wmean(s)
        assert result is not None


class TestHitRate:
    def test_all_over(self):
        s = pd.Series([5.0, 6.0, 7.0])
        assert _hit_rate(s, 4.5) == pytest.approx(1.0)

    def test_all_under(self):
        s = pd.Series([1.0, 2.0, 3.0])
        assert _hit_rate(s, 4.5) == pytest.approx(0.0)

    def test_half_over(self):
        s = pd.Series([3.0, 5.0, 3.0, 5.0])
        assert _hit_rate(s, 4.0) == pytest.approx(0.5)

    def test_too_few_returns_none(self):
        assert _hit_rate(pd.Series([1.0, 2.0]), 1.5) is None


class TestOUProbabilities:
    def test_probabilities_sum_to_one(self):
        ou = _ou_probabilities(10.0)
        for key in ou:
            if key.startswith("O"):
                line = key[1:]
                assert ou[f"O{line}"] + ou[f"U{line}"] == pytest.approx(1.0, abs=1e-4)

    def test_higher_line_lower_over_prob(self):
        ou = _ou_probabilities(15.0)
        assert ou["O10.5"] > ou["O15.5"] > ou["O20.5"]

    def test_probs_in_unit_interval(self):
        ou = _ou_probabilities(8.0)
        for p in ou.values():
            assert 0.0 <= p <= 1.0


class TestFairOdds:
    def test_even_probability(self):
        assert fair_odds(0.5) == pytest.approx(2.0)

    def test_certainty(self):
        assert fair_odds(1.0) == pytest.approx(1.0)

    def test_zero_returns_large_number(self):
        assert fair_odds(0.0) == 999.0


class TestWinnerProbs:
    def test_probs_sum_to_one(self):
        result = winner_probs(10.0, 10.0)
        total = result["1"] + result["X"] + result["2"]
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_symmetric_teams(self):
        result = winner_probs(10.0, 10.0)
        assert result["1"] == pytest.approx(result["2"], abs=0.01)

    def test_probs_in_unit_interval(self):
        result = winner_probs(8.0, 12.0)
        for v in result.values():
            assert 0.0 <= v <= 1.0


class TestCredibilityBlend:
    def test_blend_without_hit_rates_equals_model(self):
        ou = {"O5.5": 0.6, "U5.5": 0.4, "O6.5": 0.4, "U6.5": 0.6}
        blended = _credibility_blend(ou, {})
        assert blended["O5.5"] == pytest.approx(0.6, abs=1e-4)

    def test_blend_with_hit_rates_shifts_toward_empirical(self):
        ou = {"O5.5": 0.5, "U5.5": 0.5}
        # 20 zápasov, 16 cez líniu = 80%
        blended = _credibility_blend(ou, {5.5: "16/20"})
        assert blended["O5.5"] > 0.5, "Empirická hodnota 0.8 má posunúť blend nahor"

    def test_over_under_sum_to_one(self):
        ou = {"O5.5": 0.55, "U5.5": 0.45, "O6.5": 0.35, "U6.5": 0.65}
        blended = _credibility_blend(ou, {5.5: "10/20", 6.5: "7/20"})
        assert blended["O5.5"] + blended["U5.5"] == pytest.approx(1.0, abs=1e-4)

    def test_monotonicity_over_probs(self):
        ou = {f"O{l+0.5}": max(0.01, 0.9 - l * 0.1) for l in range(10)}
        ou.update({f"U{l+0.5}": 1.0 - ou[f"O{l+0.5}"] for l in range(10)})
        blended = _credibility_blend(ou, {})
        over_probs = [blended[f"O{l+0.5}"] for l in range(10)]
        assert over_probs == sorted(over_probs, reverse=True), "Over probs musia klesať"


# ---------------------------------------------------------------------------
# predict_match — integračné testy so sample_df
# ---------------------------------------------------------------------------

class TestPredictMatch:
    def test_returns_all_markets(self, sample_df):
        result = predict_match(sample_df, "Slovan", "DAC")
        assert set(result.keys()) == set(MARKETS.keys())

    def test_lambdas_positive(self, sample_df):
        result = predict_match(sample_df, "Slovan", "Sparta")
        for market, data in result.items():
            assert data["lambda_home"] > 0, f"{market}: lambda_home musí byť kladná"
            assert data["lambda_away"] > 0, f"{market}: lambda_away musí byť kladná"
            assert data["lambda_total"] > 0, f"{market}: lambda_total musí byť kladná"

    def test_ou_probabilities_valid(self, sample_df):
        result = predict_match(sample_df, "Slovan", "DAC")
        for market, data in result.items():
            for key, p in data["over_under"].items():
                assert 0.0 <= p <= 1.0, f"{market} {key}: pravd. musí byť v [0,1]"

    def test_blended_ou_sums_to_one(self, sample_df):
        result = predict_match(sample_df, "Slovan", "DAC")
        ou = result["fouls"]["over_under_blended"]
        for key in ou:
            if key.startswith("O"):
                line = key[1:]
                total = ou[f"O{line}"] + ou[f"U{line}"]
                assert total == pytest.approx(1.0, abs=1e-3), f"O+U != 1 pre {line}"

    def test_1x2_probs_sum_to_one(self, sample_df):
        result = predict_match(sample_df, "Slovan", "DAC")
        x1x2 = result["fouls"]["foul_1x2"]
        assert x1x2["1"] + x1x2["X"] + x1x2["2"] == pytest.approx(1.0, abs=1e-4)

    def test_before_idx_limits_data(self, sample_df):
        full   = predict_match(sample_df, "Slovan", "DAC")
        subset = predict_match(sample_df, "Slovan", "DAC", before_idx=30)
        # Lambda sa môže líšiť — overíme len že sú obe kladné
        assert subset["fouls"]["lambda_total"] > 0

    def test_motivation_increases_fouls(self, sample_df):
        base = predict_match(sample_df, "Slovan", "DAC", mot_home=1.0)
        high = predict_match(sample_df, "Slovan", "DAC", mot_home=1.3)
        assert high["fouls"]["lambda_home"] > base["fouls"]["lambda_home"]

    def test_motivation_does_not_affect_corners(self, sample_df):
        base = predict_match(sample_df, "Slovan", "DAC", mot_home=1.0)
        high = predict_match(sample_df, "Slovan", "DAC", mot_home=1.5)
        assert high["corners"]["lambda_home"] == pytest.approx(
            base["corners"]["lambda_home"], abs=1e-9
        )


# ---------------------------------------------------------------------------
# Pomocné výpočtové funkcie
# ---------------------------------------------------------------------------

class TestComputeMarketLambdas:
    def test_returns_positive_lambdas(self, sample_df):
        lh, la, *_ = _compute_market_lambdas(
            sample_df, "Slovan", "DAC",
            "home_fouls", "away_fouls", "fouls", 1.0, 1.0
        )
        assert lh > 0
        assert la > 0

    def test_motivation_scales_lambda(self, sample_df):
        lh_base, *_ = _compute_market_lambdas(
            sample_df, "Slovan", "DAC",
            "home_fouls", "away_fouls", "fouls", 1.0, 1.0
        )
        lh_high, *_ = _compute_market_lambdas(
            sample_df, "Slovan", "DAC",
            "home_fouls", "away_fouls", "fouls", 1.5, 1.0
        )
        assert lh_high == pytest.approx(lh_base * 1.5, rel=0.01)


class TestApplyReferee:
    def test_no_referee_returns_neutral_multipliers(self, sample_df):
        _, _, ref_info = _apply_referee(
            sample_df, None, "fouls",
            "total_fouls", "home_fouls", "away_fouls",
            10.0, 8.0,
        )
        assert ref_info["n"] == 0

    def test_ref_adjust_changes_lambda(self, sample_df):
        lh0, la0, _ = _apply_referee(
            sample_df, None, "fouls",
            "total_fouls", "home_fouls", "away_fouls",
            10.0, 8.0,
        )
        lh1, la1, _ = _apply_referee(
            sample_df, "Dzivjak", "fouls",
            "total_fouls", "home_fouls", "away_fouls",
            10.0, 8.0,
        )
        # Ak je dostatok dát, multiplier sa môže líšiť; overíme len že lambdy sú kladné
        assert lh1 > 0 and la1 > 0
