"""Shared test fixtures for hk-stock."""
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temporary directory for all tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("hkstock.core.config.DATA_DIR", data_dir)
    monkeypatch.setattr("hkstock.core.io.DATA_DIR", data_dir)
    return data_dir


@pytest.fixture
def sample_portfolio():
    """Return a sample portfolio dict for testing."""
    return {
        "total_capital_cny": 100000,
        "cash_cny": 85000,
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


@pytest.fixture
def sample_stock_data():
    """Return a sample stock analysis dict for testing."""
    return {
        "ticker": "0700.HK",
        "date": "2025-03-24",
        "price": 380.0,
        "prev_close": 375.0,
        "change_pct": 1.33,
        "volume": 15000000,
        "avg_volume": 12000000,
        "rsi": 55.0,
        "ma_short": 370.0,
        "ma_long": 360.0,
        "macd": 2.5,
        "macd_signal": 1.8,
        "bb_upper": 400.0,
        "bb_lower": 340.0,
        "adx": 28.0,
        "volume_ratio": 1.25,
        "score": 5,
        "action": "买入",
        "signals": ["均线金叉"],
        "suggested_position_cny": 10000,
        "name": "腾讯控股",
    }
