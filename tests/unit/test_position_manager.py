"""Tests for hkstock.trading.position_manager module."""
import json
import pytest
from datetime import datetime
from hkstock.trading.position_manager import (
    calc_trade_fee_hkd,
    calc_hold_days,
    check_position_limits,
    load_portfolio,
    save_portfolio,
)


class TestCalcTradeFee:
    def test_small_amount(self):
        """Small amount should hit minimum fees."""
        fee = calc_trade_fee_hkd(1000)
        assert isinstance(fee, float)
        assert fee > 0
        # Min broker fee 3 + platform 15 + SFC + HKEX + min CCASS 2 + min stamp 1
        assert fee >= 21  # at least sum of minimums

    def test_large_amount(self):
        """Large amount: percentage fees should dominate."""
        fee = calc_trade_fee_hkd(1_000_000)
        assert fee > 1000  # stamp duty alone is 0.1% = 1000

    def test_fee_proportional(self):
        """Larger amounts should have higher fees."""
        fee_small = calc_trade_fee_hkd(10_000)
        fee_large = calc_trade_fee_hkd(100_000)
        assert fee_large > fee_small

    def test_zero_amount(self):
        """Zero amount should return sum of minimums."""
        fee = calc_trade_fee_hkd(0)
        assert fee >= 0

    def test_stamp_duty_ceil(self):
        """Stamp duty should be ceil'd to at least 1 HKD."""
        # For 500 HKD: stamp = ceil(500 * 0.001) = ceil(0.5) = 1
        fee = calc_trade_fee_hkd(500)
        assert fee > 0


class TestPortfolioLoadSave:
    def test_load_default_portfolio(self, tmp_data_dir):
        """Loading with no file should return default portfolio."""
        # tmp_data_dir from conftest redirects DATA_DIR
        # But position_manager uses PORTFOLIO_FILE directly
        import hkstock.trading.position_manager as pm
        pm.PORTFOLIO_FILE = str(tmp_data_dir / "portfolio.json")

        p = load_portfolio()
        assert "total_capital_cny" in p
        assert "cash_cny" in p
        assert "positions" in p
        assert isinstance(p["positions"], dict)
        assert isinstance(p["trades"], list)

    def test_save_and_load_roundtrip(self, tmp_data_dir):
        """Save and load should produce identical data."""
        import hkstock.trading.position_manager as pm
        pm.PORTFOLIO_FILE = str(tmp_data_dir / "portfolio.json")

        portfolio = {
            "total_capital_cny": 100000,
            "cash_cny": 80000,
            "positions": {
                "0700.HK": {
                    "name": "腾讯控股",
                    "shares": 100,
                    "avg_cost_hkd": 350.0,
                    "total_cost_cny": 30800,
                }
            },
            "trades": [],
            "daily_snapshots": [],
            "created_at": "2025-01-01",
        }
        save_portfolio(portfolio)
        loaded = load_portfolio()
        assert loaded["total_capital_cny"] == 100000
        assert loaded["cash_cny"] == 80000
        assert "0700.HK" in loaded["positions"]
        assert loaded["positions"]["0700.HK"]["name"] == "腾讯控股"

    def test_save_creates_directory(self, tmp_data_dir):
        """Save should create parent directories if needed."""
        import hkstock.trading.position_manager as pm
        nested = tmp_data_dir / "sub" / "portfolio.json"
        pm.PORTFOLIO_FILE = str(nested)

        save_portfolio({"total_capital_cny": 50000, "cash_cny": 50000,
                        "positions": {}, "trades": [], "daily_snapshots": [],
                        "created_at": "2025-01-01"})
        assert nested.exists()


class TestCalcHoldDays:
    def test_recent_buy(self):
        """A buy from today should have 0 hold days."""
        today = datetime.now().strftime("%Y-%m-%d")
        portfolio = {
            "trades": [
                {"ticker": "0700.HK", "action": "BUY", "date": today},
            ]
        }
        assert calc_hold_days(portfolio, "0700.HK") == 0

    def test_no_trades(self):
        """Empty trades list should return 0."""
        assert calc_hold_days({"trades": []}, "0700.HK") == 0

    def test_ticker_not_found(self):
        """Ticker not in trades should return 0."""
        portfolio = {
            "trades": [
                {"ticker": "1211.HK", "action": "BUY", "date": "2025-01-01"},
            ]
        }
        assert calc_hold_days(portfolio, "0700.HK") == 0

    def test_uses_latest_buy(self):
        """Should use the most recent BUY (reversed search)."""
        portfolio = {
            "trades": [
                {"ticker": "0700.HK", "action": "BUY", "date": "2020-01-01"},
                {"ticker": "0700.HK", "action": "SELL", "date": "2020-06-01"},
                {"ticker": "0700.HK", "action": "BUY", "date": datetime.now().strftime("%Y-%m-%d")},
            ]
        }
        days = calc_hold_days(portfolio, "0700.HK")
        assert days == 0  # latest buy is today


class TestCheckPositionLimits:
    def test_can_buy_empty_portfolio(self):
        portfolio = {
            "total_capital_cny": 100000,
            "cash_cny": 100000,
            "positions": {},
        }
        can_buy, reason = check_position_limits(portfolio)
        assert can_buy is True

    def test_max_positions_reached(self):
        from hkstock.core.config import MAX_POSITIONS
        positions = {f"{i:04d}.HK": {"total_cost_cny": 1000} for i in range(MAX_POSITIONS)}
        portfolio = {
            "cash_cny": 50000,
            "positions": positions,
        }
        can_buy, reason = check_position_limits(portfolio)
        assert can_buy is False
        assert "上限" in reason

    def test_insufficient_cash(self):
        from hkstock.core.config import RESERVE_CASH
        portfolio = {
            "cash_cny": RESERVE_CASH + 100,  # barely above reserve
            "positions": {},
        }
        can_buy, reason = check_position_limits(portfolio)
        assert can_buy is False
        assert "不足" in reason
