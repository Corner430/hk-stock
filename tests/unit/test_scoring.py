"""Tests for hkstock.analysis.scoring module."""
from hkstock.analysis.scoring import (
    ScoreBreakdown, clamp_score, score_to_action, score_to_position_pct,
    SCORE_MIN, SCORE_MAX,
)


class TestClampScore:
    def test_within_range(self):
        assert clamp_score(5) == 5
        assert clamp_score(-5) == -5
        assert clamp_score(0) == 0

    def test_above_max(self):
        assert clamp_score(15) == SCORE_MAX
        assert clamp_score(100) == SCORE_MAX

    def test_below_min(self):
        assert clamp_score(-15) == SCORE_MIN
        assert clamp_score(-100) == SCORE_MIN

    def test_at_boundaries(self):
        assert clamp_score(SCORE_MIN) == SCORE_MIN
        assert clamp_score(SCORE_MAX) == SCORE_MAX

    def test_custom_range(self):
        assert clamp_score(10, lo=-5, hi=5) == 5
        assert clamp_score(-10, lo=-5, hi=5) == -5


class TestScoreToAction:
    def test_strong_buy(self):
        assert score_to_action(6) == "强烈买入"
        assert score_to_action(10) == "强烈买入"

    def test_buy(self):
        assert score_to_action(4) == "买入"
        assert score_to_action(5) == "买入"

    def test_tentative_buy(self):
        assert score_to_action(3) == "试探性买入"

    def test_hold(self):
        assert score_to_action(0) == "持有观望"
        assert score_to_action(1) == "持有观望"
        assert score_to_action(2) == "持有观望"

    def test_watch_reduce(self):
        assert score_to_action(-1) == "观望/减仓"
        assert score_to_action(-3) == "观望/减仓"

    def test_sell(self):
        assert score_to_action(-4) == "考虑卖出"
        assert score_to_action(-10) == "考虑卖出"


class TestScoreToPositionPct:
    def test_high_score(self):
        assert score_to_position_pct(6) == 1.0
        assert score_to_position_pct(10) == 1.0

    def test_medium_score(self):
        assert score_to_position_pct(4) == 0.7
        assert score_to_position_pct(5) == 0.7

    def test_low_buy_score(self):
        assert score_to_position_pct(3) == 0.4

    def test_no_position(self):
        assert score_to_position_pct(2) == 0.0
        assert score_to_position_pct(0) == 0.0
        assert score_to_position_pct(-5) == 0.0


class TestScoreBreakdown:
    def test_defaults_zero(self):
        b = ScoreBreakdown()
        assert b.total == 0
        assert b.clamped_total == 0

    def test_total_sum(self):
        b = ScoreBreakdown(technical=5, fundamental=2, sector_heat=1)
        assert b.total == 8

    def test_clamped_total(self):
        b = ScoreBreakdown(technical=10, fundamental=5, sector_heat=3)
        assert b.total == 18
        assert b.clamped_total == SCORE_MAX

    def test_negative_clamped(self):
        b = ScoreBreakdown(technical=-10, fundamental=-5, news=-3)
        assert b.total == -18
        assert b.clamped_total == SCORE_MIN

    def test_to_dict(self):
        b = ScoreBreakdown(technical=3, ai_adjustment=2)
        d = b.to_dict()
        assert d["technical"] == 3
        assert d["ai_adjustment"] == 2
        assert d["total"] == 5
        assert d["clamped_total"] == 5
        assert "momentum" in d
        assert "fundamental" in d
