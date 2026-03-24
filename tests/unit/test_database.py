"""Tests for hkstock.data.database module.

Uses monkeypatch to redirect DB_PATH to a temp directory,
so all tests use isolated SQLite databases.
"""
import pytest
from hkstock.data.database import (
    init_db,
    save_stocks_daily,
    save_trade,
    save_snapshot,
    save_backtest_run,
    get_latest_signals,
    get_trade_history,
    get_snapshots,
    get_all_runs,
    get_stock_history,
    get_stats_summary,
    query,
)
import hkstock.data.database as db_mod


@pytest.fixture(autouse=True)
def tmp_db(tmp_data_dir, monkeypatch):
    """Redirect DB_PATH to temp dir and initialize tables."""
    db_path = str(tmp_data_dir / "test.db")
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    # Suppress print from init_db
    init_db()
    return db_path


class TestInitDb:
    def test_tables_created(self):
        """init_db should create all 4 tables."""
        tables = query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [t["name"] for t in tables]
        assert "stocks_daily" in names
        assert "trades" in names
        assert "portfolio_snapshots" in names
        assert "backtest_runs" in names

    def test_idempotent(self):
        """Calling init_db twice should not error."""
        init_db()  # already called in fixture
        init_db()


class TestStocksDaily:
    def test_save_and_query(self):
        records = [
            {
                "date": "2025-03-24",
                "ticker": "0700.HK",
                "name": "腾讯控股",
                "price": 380.0,
                "change_pct": 1.33,
                "volume": 15000000,
                "rsi": 55.0,
                "macd": 2.5,
                "macd_signal": 1.8,
                "bb_upper": 400.0,
                "bb_lower": 340.0,
                "ma_short": 370.0,
                "ma_long": 360.0,
                "score": 5,
                "action": "买入",
                "signals": ["均线金叉"],
                "suggested_position_cny": 10000,
            }
        ]
        save_stocks_daily(records)
        result = get_latest_signals("2025-03-24")
        assert len(result) == 1
        assert result[0]["ticker"] == "0700.HK"
        assert result[0]["score"] == 5

    def test_upsert(self):
        """Inserting same date+ticker should update, not duplicate."""
        rec = {
            "date": "2025-03-24",
            "ticker": "0700.HK",
            "name": "腾讯控股",
            "price": 380.0,
            "score": 5,
        }
        save_stocks_daily([rec])
        rec["score"] = 8
        save_stocks_daily([rec])
        result = get_latest_signals("2025-03-24")
        assert len(result) == 1
        assert result[0]["score"] == 8

    def test_stock_history(self):
        records = [
            {"date": f"2025-03-{20+i:02d}", "ticker": "0700.HK", "price": 370 + i, "score": i}
            for i in range(5)
        ]
        save_stocks_daily(records)
        history = get_stock_history("0700.HK", days=10)
        assert len(history) == 5


class TestTrades:
    def test_save_and_query(self):
        trade = {
            "date": "2025-03-24",
            "action": "BUY",
            "ticker": "0700.HK",
            "name": "腾讯控股",
            "shares": 100,
            "price_hkd": 380.0,
            "cost_cny": 33800,
            "reason": "RSI超卖",
        }
        save_trade("run-001", trade)
        result = get_trade_history(run_id="run-001")
        assert len(result) == 1
        assert result[0]["ticker"] == "0700.HK"
        assert result[0]["action"] == "BUY"

    def test_filter_by_ticker(self):
        save_trade("run-001", {"date": "2025-03-24", "action": "BUY", "ticker": "0700.HK"})
        save_trade("run-001", {"date": "2025-03-24", "action": "BUY", "ticker": "1211.HK"})
        result = get_trade_history(run_id="run-001", ticker="0700.HK")
        assert len(result) == 1


class TestSnapshots:
    def test_save_and_query(self):
        snap = {
            "date": "2025-03-24",
            "cash_cny": 80000,
            "position_value_cny": 20000,
            "total_value_cny": 100000,
            "total_return_cny": 0,
            "total_return_pct": 0,
            "positions_count": 2,
        }
        save_snapshot("run-001", snap)
        result = get_snapshots("run-001")
        assert len(result) == 1
        assert result[0]["total_value_cny"] == 100000


class TestBacktestRuns:
    def test_save_and_list(self):
        meta = {
            "strategy": "v2",
            "start_date": "2025-02-01",
            "end_date": "2025-03-01",
            "initial_capital": 100000,
            "final_value": 102000,
            "return_pct": 2.0,
            "buy_count": 5,
            "sell_count": 3,
            "stop_loss_count": 1,
        }
        save_backtest_run("run-bt-001", meta)
        runs = get_all_runs()
        assert len(runs) >= 1
        found = [r for r in runs if r["run_id"] == "run-bt-001"]
        assert len(found) == 1
        assert found[0]["return_pct"] == 2.0


class TestStatsSummary:
    def test_empty(self):
        assert get_stats_summary() == {}

    def test_with_trades(self):
        save_trade("run-s", {
            "date": "2025-03-20", "action": "SELL", "ticker": "0700.HK",
            "pnl_cny": 500, "pnl_pct": 5.0,
        })
        save_trade("run-s", {
            "date": "2025-03-21", "action": "SELL", "ticker": "1211.HK",
            "pnl_cny": -200, "pnl_pct": -2.0,
        })
        stats = get_stats_summary()
        assert stats["total_trades"] == 2
        assert stats["win_count"] == 1
        assert stats["loss_count"] == 1
        assert stats["win_rate"] == 50.0
        assert stats["total_pnl"] == 300
