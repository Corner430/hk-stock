"""Tests for hkstock.core.config module."""
import pathlib
from hkstock.core import config


def test_project_root_is_path():
    assert isinstance(config.PROJECT_ROOT, pathlib.Path)


def test_project_root_has_pyproject():
    assert (config.PROJECT_ROOT / "pyproject.toml").exists()


def test_data_dir_is_path():
    assert isinstance(config.DATA_DIR, pathlib.Path)


def test_template_dir_is_path():
    assert isinstance(config.TEMPLATE_DIR, pathlib.Path)


def test_capital_constants():
    assert isinstance(config.TOTAL_CAPITAL, (int, float))
    assert config.TOTAL_CAPITAL > 0
    assert isinstance(config.MAX_POSITION, (int, float))
    assert config.MAX_POSITION > 0
    assert isinstance(config.RESERVE_CASH, (int, float))


def test_risk_constants():
    assert 0 < config.STOP_LOSS_PCT < 1
    assert 0 < config.TAKE_PROFIT_PCT < 1
    assert 0 < config.DRAWDOWN_WARN_PCT < config.DRAWDOWN_HALT_PCT < config.DRAWDOWN_REDUCE_PCT


def test_rsi_constants():
    assert 0 < config.RSI_OVERSOLD < config.RSI_OVERBOUGHT < 100
    assert config.RSI_PERIOD > 0


def test_backtest_constants():
    assert 0 < config.BT_HKD_CNY_RATE < 2
    assert config.BT_DEFAULT_LOT_SIZE > 0
    assert 0 < config.BT_SLIPPAGE_PCT < 1
    assert 0 < config.BT_SURVIVORSHIP_DISCOUNT <= 1


def test_holidays_non_empty():
    assert len(config.HK_HOLIDAYS) > 0
    # Check format
    for h in config.HK_HOLIDAYS:
        assert len(h) == 10  # "YYYY-MM-DD"
        assert h[4] == "-" and h[7] == "-"
