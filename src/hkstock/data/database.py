"""
数据库层 - SQLite 持久化存储
表结构：
  stocks_daily   — 每日股票行情 + 指标
  trades         — 交易记录
  portfolio_snapshots — 每日资产快照
  backtest_runs  — 回测运行记录（方便对比不同策略）
"""
from __future__ import annotations

import sqlite3
import json
import os
import logging
from contextlib import contextmanager
from datetime import datetime
from hkstock.core.config import DATA_DIR

DB_PATH = str(DATA_DIR / "hkstock.db")

@contextmanager
def get_conn():
    """获取数据库连接（上下文管理器，自动关闭）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # 让结果可以用列名访问
    conn.execute("PRAGMA journal_mode=WAL")  # 写多读多更安全
    try:
        yield conn
    finally:
        conn.close()

def init_db() -> None:
    """建表（幂等，已存在不报错）"""
    with get_conn() as conn:
        c = conn.cursor()

        # ── 1. 每日股票行情 + 技术指标 ──
        c.execute("""
        CREATE TABLE IF NOT EXISTS stocks_daily (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            name        TEXT,
            price       REAL,
            change_pct  REAL,
            volume      REAL,
            rsi         REAL,
            macd        REAL,
            macd_signal REAL,
            bb_upper    REAL,
            bb_lower    REAL,
            ma10        REAL,
            ma30        REAL,
            score       INTEGER,
            action      TEXT,
            signals     TEXT,        -- JSON 数组
            suggested_position_cny REAL,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, ticker)     -- 同一天同一只股票只存一条
        )""")

        # ── 2. 交易记录 ──
        c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT,        -- 关联回测或实盘的运行ID
            date        TEXT NOT NULL,
            action      TEXT NOT NULL,  -- BUY / SELL
            ticker      TEXT NOT NULL,
            name        TEXT,
            shares      INTEGER,
            price_hkd   REAL,
            cost_cny    REAL,        -- 买入总成本（含手续费）
            revenue_cny REAL,        -- 卖出收入
            pnl_cny     REAL,        -- 盈亏（卖出时）
            pnl_pct     REAL,
            reason      TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )""")

        # ── 3. 每日资产快照（净值曲线数据）──
        c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT,
            date            TEXT NOT NULL,
            cash_cny        REAL,
            position_value_cny REAL,
            total_value_cny REAL,
            total_return_cny REAL,
            total_return_pct REAL,
            positions_count INTEGER,
            positions_detail TEXT,   -- JSON，持仓明细快照
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(run_id, date)
        )""")

        # ── 4. 回测/实盘运行记录 ──
        c.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT UNIQUE NOT NULL,
            strategy        TEXT,    -- v1 / v2 / live
            start_date      TEXT,
            end_date        TEXT,
            initial_capital REAL,
            final_value     REAL,
            return_pct      REAL,
            buy_count       INTEGER,
            sell_count       INTEGER,
            stop_loss_count INTEGER,
            notes           TEXT,    -- JSON，可以存任意附加信息
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )""")

        conn.commit()
    print(f"✅ 数据库初始化完成: {DB_PATH}")

# ─────────────────────────────────────────────
# 写入函数
# ─────────────────────────────────────────────

def save_stocks_daily(records: list[dict]) -> None:
    """批量保存每日分析结果（来自 analyzer.py 的 stocks 列表）"""
    with get_conn() as conn:
        c = conn.cursor()
        for r in records:
            c.execute("""
            INSERT INTO stocks_daily
                (date, ticker, name, price, change_pct, volume,
                 rsi, macd, macd_signal, bb_upper, bb_lower, ma10, ma30,
                 score, action, signals, suggested_position_cny)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, ticker) DO UPDATE SET
                price=excluded.price, change_pct=excluded.change_pct,
                score=excluded.score, action=excluded.action,
                rsi=excluded.rsi, macd=excluded.macd,
                signals=excluded.signals, suggested_position_cny=excluded.suggested_position_cny
            """, (
                r.get("date"), r.get("ticker"), r.get("name"),
                r.get("price"), r.get("change_pct"), r.get("volume"),
                r.get("rsi"), r.get("macd"), r.get("macd_signal"),
                r.get("bb_upper"), r.get("bb_lower"),
                r.get("ma_short"), r.get("ma_long"),
                r.get("score"), r.get("action"),
                json.dumps(r.get("signals", []), ensure_ascii=False),
                r.get("suggested_position_cny", 0),
            ))
        conn.commit()

def save_trade(run_id: str, trade: dict) -> None:
    """保存一笔交易"""
    with get_conn() as conn:
        conn.execute("""
        INSERT INTO trades (run_id, date, action, ticker, name, shares,
            price_hkd, cost_cny, revenue_cny, pnl_cny, pnl_pct, reason)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_id, trade.get("date"), trade.get("action"),
            trade.get("ticker"), trade.get("name"), trade.get("shares"),
            trade.get("price_hkd"), trade.get("cost_cny", 0),
            trade.get("revenue_cny", 0), trade.get("pnl_cny", 0),
            trade.get("pnl_pct", 0), trade.get("reason", ""),
        ))
        conn.commit()

def save_snapshot(run_id: str, snap: dict, positions_detail: dict | None = None) -> None:
    """保存每日资产快照"""
    with get_conn() as conn:
        conn.execute("""
        INSERT INTO portfolio_snapshots
            (run_id, date, cash_cny, position_value_cny, total_value_cny,
             total_return_cny, total_return_pct, positions_count, positions_detail)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id, date) DO UPDATE SET
            total_value_cny=excluded.total_value_cny,
            total_return_pct=excluded.total_return_pct,
            positions_detail=excluded.positions_detail
        """, (
            run_id, snap.get("date"),
            snap.get("cash_cny"), snap.get("position_value_cny"),
            snap.get("total_value_cny"), snap.get("total_return_cny"),
            snap.get("total_return_pct"), snap.get("positions_count"),
            json.dumps(positions_detail or {}, ensure_ascii=False),
        ))
        conn.commit()

def save_backtest_run(run_id: str, meta: dict) -> None:
    """保存回测汇总"""
    with get_conn() as conn:
        conn.execute("""
        INSERT INTO backtest_runs
            (run_id, strategy, start_date, end_date, initial_capital,
             final_value, return_pct, buy_count, sell_count, stop_loss_count, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET
            final_value=excluded.final_value,
            return_pct=excluded.return_pct
        """, (
            run_id, meta.get("strategy"), meta.get("start_date"), meta.get("end_date"),
            meta.get("initial_capital", 100000), meta.get("final_value"),
            meta.get("return_pct"), meta.get("buy_count", 0),
            meta.get("sell_count", 0), meta.get("stop_loss_count", 0),
            json.dumps(meta.get("notes", {}), ensure_ascii=False),
        ))
        conn.commit()

# ─────────────────────────────────────────────
# 查询函数
# ─────────────────────────────────────────────

def query(sql: str, params: tuple = ()) -> list[dict]:
    """通用查询，返回 list[dict]"""
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

def get_latest_signals(date: str | None = None) -> list[dict]:
    """获取最新一天的信号（默认最新日期）"""
    if date:
        return query("SELECT * FROM stocks_daily WHERE date=? ORDER BY score DESC", (date,))
    return query("""
        SELECT * FROM stocks_daily WHERE date=(SELECT MAX(date) FROM stocks_daily)
        ORDER BY score DESC
    """)

def get_trade_history(run_id: str | None = None, ticker: str | None = None, limit: int = 50) -> list[dict]:
    """查交易记录"""
    if run_id and ticker:
        return query("SELECT * FROM trades WHERE run_id=? AND ticker=? ORDER BY date DESC LIMIT ?",
                     (run_id, ticker, limit))
    elif run_id:
        return query("SELECT * FROM trades WHERE run_id=? ORDER BY date DESC LIMIT ?", (run_id, limit))
    return query("SELECT * FROM trades ORDER BY date DESC LIMIT ?", (limit,))

def get_snapshots(run_id: str) -> list[dict]:
    """获取某次回测的净值曲线"""
    return query("SELECT * FROM portfolio_snapshots WHERE run_id=? ORDER BY date", (run_id,))

def get_all_runs() -> list[dict]:
    """列出所有回测记录"""
    return query("SELECT * FROM backtest_runs ORDER BY created_at DESC")

def get_stock_history(ticker: str, days: int = 30) -> list[dict]:
    """查某只股票的历史评分变化"""
    return query("""
        SELECT date, price, change_pct, rsi, score, action, signals
        FROM stocks_daily WHERE ticker=?
        ORDER BY date DESC LIMIT ?
    """, (ticker, days))

def get_stats_summary() -> dict:
    """统计摘要：胜率、平均盈亏等"""
    sells = query("SELECT pnl_cny, pnl_pct FROM trades WHERE action='SELL' AND pnl_cny IS NOT NULL")
    if not sells:
        return {}
    wins = [s for s in sells if s["pnl_cny"] > 0]
    losses = [s for s in sells if s["pnl_cny"] <= 0]
    return {
        "total_trades": len(sells),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(sells) * 100, 1),
        "avg_win_pct": round(sum(s["pnl_pct"] for s in wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct": round(sum(s["pnl_pct"] for s in losses) / len(losses), 2) if losses else 0,
        "total_pnl": round(sum(s["pnl_cny"] for s in sells), 2),
    }

if __name__ == "__main__":
    init_db()

    # 测试写入和查询
    run_id = "test_" + datetime.now().strftime("%Y%m%d%H%M%S")

    save_backtest_run(run_id, {
        "strategy": "v2", "start_date": "2026-02-25", "end_date": "2026-03-05",
        "initial_capital": 100000, "final_value": 99159, "return_pct": -0.84,
        "buy_count": 7, "sell_count": 1, "stop_loss_count": 0,
    })
    save_trade(run_id, {
        "date": "2026-02-27", "action": "BUY", "ticker": "2318.HK",
        "name": "中国平安", "shares": 100, "price_hkd": 68.0,
        "cost_cny": 6232, "reason": "RSI超卖+均线金叉",
    })
    save_snapshot(run_id, {
        "date": "2026-03-05", "cash_cny": 52921,
        "position_value_cny": 46238, "total_value_cny": 99159,
        "total_return_cny": -841, "total_return_pct": -0.84,
        "positions_count": 4,
    })

    print("\n📋 所有回测记录:")
    for r in get_all_runs():
        print(f"  {r['run_id']}  策略:{r['strategy']}  收益:{r['return_pct']:+.2f}%")

    print("\n📊 交易统计:")
    stats = get_stats_summary()
    print(f"  {stats}")

    print("\n✅ 数据库测试通过")
