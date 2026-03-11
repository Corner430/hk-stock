"""
港股回测引擎（统一版）
评分逻辑与实盘 analyzer.py 完全一致（含动量、量价背离增强）

⚠️ 注意事项：
  1. 仅包含技术面 + 动量评分，未包含基本面/AI/板块/大盘/南向资金等调整
  2. 存在生存者偏差（survivorship bias）：回测池来自当前活跃股票，
     已退市/暂停交易的股票无法被纳入，导致回测结果偏乐观
  3. 建议结合多区间回测 + 实盘模拟验证策略有效性

支持多种回测模式：
  python backtest.py weekly           # 最近一周真实数据回测
  python backtest.py multiwindow      # 多区间回测（防过拟合）
  python backtest.py full             # 完整回测（默认）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import math
import pandas as pd
import numpy as np
from real_data import fetch_history
from indicators import calc_rsi, calc_macd, calc_bollinger, calc_adx, calc_atr, calc_momentum
from position_manager import calc_trade_fee_hkd
import config

# 交易费用估算（按每笔 10000 HKD 计算双边费率）
_FEE_BASE_AMOUNT = 10000
_SINGLE_SIDE_FEE_PCT = calc_trade_fee_hkd(_FEE_BASE_AMOUNT) / _FEE_BASE_AMOUNT * 100
_ROUND_TRIP_FEE_PCT = _SINGLE_SIDE_FEE_PCT * 2  # 买卖双边

# 滑点模拟（单边）
SLIPPAGE_PCT = 0.05  # 0.05% 单边滑点（港股流动性较好的股票）


def backtest_analyze(df, ticker, config_obj):
    """
    对单只股票在给定数据上执行技术分析，返回每日信号。
    评分逻辑与 analyzer.analyze_stock 完全一致（含增强因子）。
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
    momentum = calc_momentum(close, period=config_obj.MOMENTUM_PERIOD)

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

        # 成交量信号（增强权重：±2）
        vol_i = float(volume.iloc[i]) if not pd.isna(volume.iloc[i]) else 0
        avg_v = float(avg_vol.iloc[i]) if not pd.isna(avg_vol.iloc[i]) else 0
        vol_ratio = vol_i / avg_v if avg_v > 0 else 1.0
        chg = float(change_pct.iloc[i]) if not pd.isna(change_pct.iloc[i]) else 0
        if vol_ratio > config_obj.VOLUME_SPIKE:
            if chg > 0:
                score += 2
            else:
                score -= 2

        # 量价背离检测
        if i >= 10:
            recent_5_price = close.iloc[i-4:i+1].mean()
            prev_5_price = close.iloc[i-9:i-4].mean()
            recent_5_vol = volume.iloc[i-4:i+1].mean()
            prev_5_vol = volume.iloc[i-9:i-4].mean()
            if prev_5_vol > 0:
                price_up = recent_5_price > prev_5_price
                vol_shrink = recent_5_vol < prev_5_vol * 0.7
                if price_up and vol_shrink:
                    score -= 1  # 量价背离
                price_down = recent_5_price < prev_5_price
                if price_down and vol_shrink:
                    score += 1  # 缩量下跌

        # 动量因子
        mom_i = float(momentum.iloc[i]) if not pd.isna(momentum.iloc[i]) else 0
        if mom_i > 10:
            score += 2
        elif mom_i > 5:
            score += 1
        elif mom_i < -10:
            score -= 1

        # 评分钳位（与实盘一致）
        score = max(-10, min(10, score))

        daily_signals.append({
            "date": df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], "strftime") else str(df.index[i]),
            "price": price_i,
            "rsi": round(r, 2),
            "score": score,
            "ticker": ticker,
            "momentum": round(mom_i, 2),
        })

    return daily_signals


# ── 风险指标计算 ──────────────────────────────────────────

def _calc_risk_metrics(daily_returns: list[float], annual_factor: int = 252) -> dict:
    """
    计算 Sharpe 比率、Calmar 比率、最大回撤等风险指标。
    daily_returns: 每日收益率列表（百分比形式，如 1.5 表示 +1.5%）
    """
    if not daily_returns or len(daily_returns) < 5:
        return {"sharpe": 0, "calmar": 0, "max_drawdown_pct": 0, "annual_return_pct": 0}

    returns = np.array(daily_returns) / 100  # 转为小数
    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)

    # 年化收益
    annual_return = mean_ret * annual_factor
    annual_std = std_ret * math.sqrt(annual_factor)

    # Sharpe ratio（假设无风险利率 3.5%/年，港元存款利率近似）
    risk_free_daily = 0.035 / annual_factor
    sharpe = (mean_ret - risk_free_daily) / std_ret * math.sqrt(annual_factor) if std_ret > 0 else 0

    # 最大回撤
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - peak) / peak
    max_dd = float(np.min(drawdowns))

    # Calmar ratio = 年化收益 / 最大回撤绝对值
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0

    return {
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "annual_return_pct": round(annual_return * 100, 2),
        "annual_volatility_pct": round(annual_std * 100, 2),
    }


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
    print(f"⚠️ 生存者偏差警告: 回测池来自当前活跃股票，已退市股票未纳入")
    print(f"  交易费用: {_ROUND_TRIP_FEE_PCT:.3f}% (双边)  滑点: {SLIPPAGE_PCT*2:.2f}% (双边)")
    print(f"{'='*60}\n")

    if mode == "weekly":
        return _run_weekly(tickers, days=days)
    elif mode == "multiwindow":
        return _run_multiwindow(tickers, days=days, window_size=window_size)
    else:
        return _run_full(tickers, days=days)


def _run_full(tickers, days=90):
    """完整回测：买入门槛>=7（与实盘一致），卖出门槛<=-3，含滑点+费用"""
    results = {"mode": "full", "tickers": [], "summary": {}}
    all_trades = []
    daily_portfolio_returns = []

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
                # 买入加滑点
                buy_price = sig["price"] * (1 + SLIPPAGE_PCT / 100)
                all_trades.append({
                    "date": sig["date"], "ticker": ticker,
                    "action": "BUY", "price": sig["price"],
                    "exec_price": round(buy_price, 4),
                    "score": sig["score"],
                })
            elif holding and sig["score"] <= -3:
                holding = False
                # 卖出减滑点
                sell_price = sig["price"] * (1 - SLIPPAGE_PCT / 100)
                pnl_pct = (sell_price / buy_price - 1) * 100 - _ROUND_TRIP_FEE_PCT if buy_price > 0 else 0
                all_trades.append({
                    "date": sig["date"], "ticker": ticker,
                    "action": "SELL", "price": sig["price"],
                    "exec_price": round(sell_price, 4),
                    "score": sig["score"],
                    "pnl_pct": round(pnl_pct, 2),
                })
                daily_portfolio_returns.append(pnl_pct)

        results["tickers"].append(ticker)

    # 统计
    sells = [t for t in all_trades if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]
    pnl_list = [t.get("pnl_pct", 0) for t in sells]

    # 风险指标
    risk_metrics = _calc_risk_metrics(daily_portfolio_returns)

    results["summary"] = {
        "total_trades": len(all_trades),
        "sell_trades": len(sells),
        "win_rate": round(len(wins) / max(1, len(sells)) * 100, 1),
        "avg_pnl": round(sum(pnl_list) / max(1, len(sells)), 2),
        "total_pnl": round(sum(pnl_list), 2),
        "max_single_win": round(max(pnl_list) if pnl_list else 0, 2),
        "max_single_loss": round(min(pnl_list) if pnl_list else 0, 2),
        "fee_pct_roundtrip": round(_ROUND_TRIP_FEE_PCT, 3),
        "slippage_pct_roundtrip": round(SLIPPAGE_PCT * 2, 3),
        **risk_metrics,
        "survivorship_bias_warning": True,
    }
    results["trades"] = all_trades

    print(f"\n回测结果:")
    print(f"  总交易次数: {results['summary']['total_trades']}")
    print(f"  卖出次数: {results['summary']['sell_trades']}")
    print(f"  胜率: {results['summary']['win_rate']}%")
    print(f"  平均盈亏: {results['summary']['avg_pnl']}%")
    print(f"  累计盈亏: {results['summary']['total_pnl']}%")
    print(f"  最大单笔盈利: +{results['summary']['max_single_win']}%")
    print(f"  最大单笔亏损: {results['summary']['max_single_loss']}%")
    print(f"\n  风险指标:")
    print(f"    Sharpe Ratio:  {results['summary']['sharpe']}")
    print(f"    Calmar Ratio:  {results['summary']['calmar']}")
    print(f"    最大回撤:       {results['summary']['max_drawdown_pct']}%")
    print(f"    年化收益:       {results['summary']['annual_return_pct']}%")
    print(f"    年化波动:       {results['summary'].get('annual_volatility_pct', 0)}%")
    print(f"\n  ⚠️ 以上结果存在生存者偏差，实际表现可能不如回测")

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
