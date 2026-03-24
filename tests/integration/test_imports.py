"""Integration test: verify all package modules can be imported."""


def test_core_imports():
    from hkstock.core import config
    from hkstock.core.config import PROJECT_ROOT, DATA_DIR, TEMPLATE_DIR
    from hkstock.core.types import StockAnalysis, Portfolio, TradeRecord
    from hkstock.core.io import read_json, write_json
    from hkstock.core.logging import setup_logging, get_logger
    from hkstock.analysis.scoring import ScoreBreakdown, clamp_score, score_to_action


def test_data_imports():
    from hkstock.data import real_data, database, market_data


def test_analysis_imports():
    from hkstock.analysis import indicators, fundamentals, ai_analyzer
    from hkstock.analysis.sector import get_sector


def test_strategy_imports():
    from hkstock.strategy import analyzer, screener, ipo_tracker
    from hkstock.strategy.backtest import run_backtest


def test_trading_imports():
    from hkstock.trading import auto_trader, position_manager


def test_app_imports():
    from hkstock.app import daily_report, dashboard, cron


def test_project_root_valid():
    from hkstock.core.config import PROJECT_ROOT
    assert PROJECT_ROOT.exists()
    assert (PROJECT_ROOT / "pyproject.toml").exists()
