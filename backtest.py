"""
港股回测引擎（统一版）
评分逻辑与实盘 analyzer.py 完全一致

注意：当前回测仅包含技术面评分，未包含基本面/AI/板块/大盘等调整。
回测结果可能与实盘策略存在偏差。后续可通过传入调整函数进行增强。

支持多种回测模式：
  python backtest.py weekly           # 最近一周真实数据回测
  python backtest.py multiwindow      # 多区间回测（防过拟合）
  python backtest.py full             # 完整回测（默认）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
from real_data import fetch_history
from indicators import calc_rsi, calc_macd, calc_bollinger, calc_adx
from position_manager import calc_trade_fee_hkd
import config

# 交易费用估算（按每笔 10000 HKD 计算双边费率）
_FEE_BASE_AMOUNT = 10000
_SINGLE_SIDE_FEE_PCT = calc_trade_fee_hkd(_FEE_BASE_AMOUNT) / _FEE_BASE_AMOUNT * 100
_ROUND_TRIP_FEE_PCT = _SINGLE_SIDE_FEE_PCT * 2  # 买卖双边


def backtest_analyze(df, ticker, config_obj):
    """
    对单只股票在给定数据上执行技术分析，返回每日信号。
    评分逻辑与 analyzer.analyze_stock 完全一致。
    注意：仅包含技术面评分，未包含基本面/AI/板块/大盘调整。
    """
    if df is None or len(df) < 30:
        return []

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    rsi = calc_rsi(close, config_obj.RSI_PERIOD)
    ma_short = close.rolling(config_obj.MA_SHORT).mean()
    ma_long = close.rolling(config_obj.MA_LONG).mean()
    macd, signal_line, histogram = calc_macd(close)
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)
    adx, plus_di, minus_di = calc_adx(high, low, close)
    avg_vol = volume.rolling(20).mean()

    # ChangePercent
    change_pct = close.pct_change() * 100

    daily_signals = []
    for i in range(30, len(df)):
        score = 0

        # RSI 渐进式评分
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        if r < config_obj.RSI_OVERSOLD:
            score += 3
        elif r > 80:
            score -= 4
        elif r > config_obj.RSI_OVERBOUGHT:
            score -= 2

        # 均线金叉/死叉（与实盘一致：金叉+2, 死叉-2）
        ms = float(ma_short.iloc[i]) if not pd.isna(ma_short.iloc[i]) else 0
        ml = float(ma_long.iloc[i]) if not pd.isna(ma_long.iloc[i]) else 0
        if ms > ml > 0:
            score += 2
        elif 0 < ms < ml:
            score -= 2

        # MACD 信号
        m = float(macd.iloc[i]) if not pd.isna(macd.iloc[i]) else 0
        s = float(signal_line.iloc[i]) if not pd.isna(signal_line.iloc[i]) else 0
        if m > s and m > 0:
            score += 2
        elif m > s:
            score += 1
        elif m < s:
            score -= 2

        # 布林带信号
        bbu = float(bb_upper.iloc[i]) if not pd.isna(bb_upper.iloc[i]) else 0
        bbl = float(bb_lower.iloc[i]) if not pd.isna(bb_lower.iloc[i]) else 0
        price_i = float(close.iloc[i])
        if bbl > 0 and price_i < bbl:
            score += 2
        elif bbu > 0 and price_i > bbu:
            score -= 2

        # ADX 趋势强度信号
        adx_i = float(adx.iloc[i]) if not pd.isna(adx.iloc[i]) else 0
        if adx_i > 25:
            if score > 0:
                score += 1
            elif score < 0:
                score -= 1

        # 成交量信号
        vol_i = float(volume.iloc[i]) if not pd.isna(volume.iloc[i]) else 0
        avg_v = float(avg_vol.iloc[i]) if not pd.isna(avg_vol.iloc[i]) else 0
        vol_ratio = vol_i / avg_v if avg_v > 0 else 1.0
        chg = float(change_pct.iloc[i]) if not pd.isna(change_pct.iloc[i]) else 0
        if vol_ratio > config_obj.VOLUME_SPIKE:
            if chg > 0:
                score += 1
            else:
                score -= 1

        # 评分钳位（与实盘一致）
        score = max(-10, min(10, score))

        daily_signals.append({
            "date": df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], "strftime") else str(df.index[i]),
            "price": price_i,
            "rsi": round(r, 2),
            "score": score,
            "ticker": ticker,
        })

    return daily_signals


def run_backtest(mode="full", days=90, window_size=7, tickers=None):
    """
    统一回测入口
    mode: "full"=完整回测, "weekly"=最近一周, "multiwindow"=多区间
    """
    if tickers is None:
        from stock_screener import get_dynamic_watchlist
        tickers, _ = get_dynamic_watchlist(top_n=100)
        if not tickers:
            raise RuntimeError("动态筛选未返回任何股票，回测终止")

    print(f"\n{'='*60}")
    print(f"回测模式: {mode}  |  股票数: {len(tickers)}  |  历史天数: {days}")
    print(f"{'='*60}\n")

    if mode == "weekly":
        return _run_weekly(tickers, days=days)
    elif mode == "multiwindow":
        return _run_multiwindow(tickers, days=days, window_size=window_size)
    else:
        return _run_full(tickers, days=days)


def _run_full(tickers, days=90):
    """完整回测：买入门槛>=7（与实盘一致），卖出门槛<=-3"""
    results = {"mode": "full", "tickers": [], "summary": {}}
    all_trades = []

    for ticker in tickers:
        df = fetch_history(ticker, days=days)
        signals = backtest_analyze(df, ticker, config)
        if not signals:
            continue

        # 回测逻辑：评分>=7买入（与实盘买入门槛一致），评分<=-3卖出
        holding = False
        buy_price = 0
        for sig in signals:
            if not holding and sig["score"] >= 7:
                holding = True
                buy_price = sig["price"]
                all_trades.append({
                    "date": sig["date"], "ticker": ticker,
                    "action": "BUY", "price": sig["price"], "score": sig["score"],
                })
            elif holding and sig["score"] <= -3:
                holding = False
                pnl_pct = (sig["price"] / buy_price - 1) * 100 - _ROUND_TRIP_FEE_PCT if buy_price > 0 else 0
                all_trades.append({
                    "date": sig["date"], "ticker": ticker,
                    "action": "SELL", "price": sig["price"], "score": sig["score"],
                    "pnl_pct": round(pnl_pct, 2),
                })

        results["tickers"].append(ticker)

    # 统计
    sells = [t for t in all_trades if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]
    results["summary"] = {
        "total_trades": len(all_trades),
        "sell_trades": len(sells),
        "win_rate": round(len(wins) / max(1, len(sells)) * 100, 1),
        "avg_pnl": round(sum(t.get("pnl_pct", 0) for t in sells) / max(1, len(sells)), 2),
    }
    results["trades"] = all_trades

    print(f"\n回测结果:")
    print(f"  总交易次数: {results['summary']['total_trades']}")
    print(f"  卖出次数: {results['summary']['sell_trades']}")
    print(f"  胜率: {results['summary']['win_rate']}%")
    print(f"  平均盈亏: {results['summary']['avg_pnl']}%")

    return results


def _run_weekly(tickers, days=30):
    """一周回测"""
    results = {"mode": "weekly", "tickers": [], "daily_pnl": []}

    for ticker in tickers:
        df = fetch_history(ticker, days=days)
        signals = backtest_analyze(df, ticker, config)
        if signals:
            results["tickers"].append(ticker)
            recent = signals[-7:] if len(signals) >= 7 else signals
            for sig in recent:
                results["daily_pnl"].append(sig)

    print(f"\n一周回测: 分析 {len(results['tickers'])} 只股票，{len(results['daily_pnl'])} 条信号")
    return results


def _run_multiwindow(tickers, days=90, window_size=7):
    """多区间回测（防过拟合）"""
    results = {"mode": "multiwindow", "windows": []}

    for ticker in tickers[:5]:
        df = fetch_history(ticker, days=days)
        signals = backtest_analyze(df, ticker, config)
        if not signals or len(signals) < window_size * 4:
            continue

        step = len(signals) // 4
        for w in range(4):
            window = signals[w * step: (w + 1) * step]
            buys = sum(1 for s in window if s["score"] >= 7)
            sells = sum(1 for s in window if s["score"] <= -3)
            results["windows"].append({
                "ticker": ticker,
                "window": w + 1,
                "start": window[0]["date"],
                "end": window[-1]["date"],
                "buy_signals": buys,
                "sell_signals": sells,
            })

    print(f"\n多区间回测: {len(results['windows'])} 个窗口")
    for w in results["windows"]:
        print(f"  {w['ticker']} 窗口{w['window']}: {w['start']}~{w['end']}  买{w['buy_signals']}次 卖{w['sell_signals']}次")

    return results


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    run_backtest(mode=mode)
