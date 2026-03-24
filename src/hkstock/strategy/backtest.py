"""
港股回测引擎 v2（统一版）
与实盘 auto_trader.py / position_manager.py 逻辑对齐：
  - 组合级资金管理（现金追踪、仓位上限、保留现金）
  - 分批建仓 (30/30/40)
  - 完整退出体系：止损、跟踪止损、时间止损、分批止盈、评分卖出
  - 组合回撤保护（警告/停买/强制减仓）
  - 板块集中度限制（每板块最多 2 只）
  - 市场状态机（牛/熊/震荡动态调节买入门槛与仓位）
  - 止损冷却期 & 连亏熔断 & 单日亏损上限
  - 评分增强：ADX 弱趋势削弱、大跌过滤、MA 斜率确认、RSI 量能确认
  - 周线级别确认

⚠️ 注意事项：
  1. 仅包含技术面评分，未包含基本面/AI/板块热度/南向资金等调整
  2. 存在生存者偏差（survivorship bias）：回测池来自当前活跃股票
  3. 使用固定汇率 HKD_CNY_RATE=0.88，不调用实时接口

支持多种回测模式：
  python backtest.py weekly           # 最近一周真实数据回测
  python backtest.py multiwindow      # 多区间回测（防过拟合）
  python backtest.py full             # 完整回测（默认）
"""
import sys
import os

import math
import copy
from datetime import datetime as _dt
import pandas as pd
import numpy as np
from hkstock.data.real_data import fetch_history
from hkstock.analysis.indicators import (
    calc_rsi, calc_macd, calc_bollinger, calc_adx, calc_atr,
    calc_momentum, resample_to_weekly,
)
from hkstock.trading.position_manager import calc_trade_fee_hkd
from hkstock.analysis.sector import get_sector
from hkstock.core import config

# ── 常量 ────────────────────────────────────────────────────
HKD_CNY_RATE = 0.88             # 回测固定汇率（不调 API）
DEFAULT_LOT_SIZE = 100           # 港股默认每手股数
SLIPPAGE_PCT = 0.05              # 0.05% 单边滑点

# 生存者偏差折扣
SURVIVORSHIP_BIAS_DISCOUNT = 0.7  # 30% 折扣

# Phase B: 冷却 & 熔断
COOLDOWN_DAYS = 10               # 止损后冷却交易日数
MAX_CONSECUTIVE_LOSSES = 4       # 连亏熔断触发
CIRCUIT_BREAKER_DAYS = 5         # 熔断暂停交易日数
DAILY_LOSS_LIMIT_PCT = 0.03      # 单日亏损上限 3%

# Phase B: 板块集中度
MAX_PER_SECTOR = 2               # 每板块最多 2 只

# Phase D: 交易频率控制
MAX_NEW_POSITIONS_PER_WEEK = 2   # 每周最多新开2只（分批续建不算）
MIN_OPEN_AMOUNT_CNY = 5000       # 最低开仓金额（费用覆盖门槛）


# ── 市场状态计算 ──────────────────────────────────────────────

def _calc_market_regime(days):
    """
    计算恒生指数每日市场状态（bullish / bearish / neutral）。
    返回 dict: {date_str: regime_str}
    """
    hsi_df = fetch_history("HSI.HI", days=max(days, 120))
    if hsi_df is None or len(hsi_df) < 35:
        return {}

    close = hsi_df["Close"]
    ma10 = close.rolling(10).mean()
    ma30 = close.rolling(30).mean()
    rsi = calc_rsi(close, 14)

    regime_map = {}
    for i in range(30, len(hsi_df)):
        date_str = hsi_df.index[i].strftime("%Y-%m-%d") if hasattr(
            hsi_df.index[i], "strftime") else str(hsi_df.index[i])
        m10 = float(ma10.iloc[i]) if not pd.isna(ma10.iloc[i]) else 0
        m30 = float(ma30.iloc[i]) if not pd.isna(ma30.iloc[i]) else 0
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50

        if m10 > m30 and r > 50:
            regime_map[date_str] = "bullish"
        elif m10 < m30 and r < 45:
            regime_map[date_str] = "bearish"
        else:
            regime_map[date_str] = "neutral"

    return regime_map


# ── 评分引擎 ──────────────────────────────────────────────────

def backtest_analyze(df, ticker, config_obj,
                     market_regime_map=None,
                     weekly_bias_map=None):
    """
    对单只股票在给定数据上执行技术分析，返回每日信号列表。
    评分逻辑与 analyzer.analyze_stock 对齐（含 Phase B 增强）。

    weekly_bias_map: {date_str: +1/0/-1} 周线多空偏向
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
    change_pct = close.pct_change() * 100

    daily_signals = []
    for i in range(30, len(df)):
        score = 0

        # ── RSI 渐进式评分 ──
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        vol_i = float(volume.iloc[i]) if not pd.isna(volume.iloc[i]) else 0
        avg_v = float(avg_vol.iloc[i]) if not pd.isna(avg_vol.iloc[i]) else 0
        vol_ratio = vol_i / avg_v if avg_v > 0 else 1.0

        # Phase B6.2: RSI 超卖需量能确认
        if r < config_obj.RSI_OVERSOLD:
            score += 3 if vol_ratio > 1.2 else 2
        elif r > 80:
            score -= 4
        elif r > config_obj.RSI_OVERBOUGHT:
            score -= 2

        # ── ADX 趋势强度（提前计算，供趋势复合因子使用） ──
        adx_i = float(adx.iloc[i]) if not pd.isna(adx.iloc[i]) else 0

        # ── 趋势复合因子（合并均线/MACD/动量，解决共线性） ──
        ms = float(ma_short.iloc[i]) if not pd.isna(ma_short.iloc[i]) else 0
        ml = float(ma_long.iloc[i]) if not pd.isna(ma_long.iloc[i]) else 0
        m = float(macd.iloc[i]) if not pd.isna(macd.iloc[i]) else 0
        s = float(signal_line.iloc[i]) if not pd.isna(signal_line.iloc[i]) else 0
        mom_i = float(momentum.iloc[i]) if not pd.isna(momentum.iloc[i]) else 0

        # 子信号1: 均线趋势
        trend_ma = 0
        if ms > ml > 0:
            ms_prev = float(ma_short.iloc[i - 1]) if i > 0 and not pd.isna(
                ma_short.iloc[i - 1]) else 0
            ml_prev = float(ma_long.iloc[i - 1]) if i > 0 and not pd.isna(
                ma_long.iloc[i - 1]) else 0
            if ms > ms_prev and ml > ml_prev:
                trend_ma = 1    # 双线上升金叉
            else:
                trend_ma = 0.5  # 仅位置关系
        elif 0 < ms < ml:
            trend_ma = -1

        # 子信号2: MACD 趋势
        trend_macd = 0
        if m > s and m > 0:
            trend_macd = 1
        elif m > s:
            trend_macd = 0.5
        elif m < s:
            trend_macd = -1

        # 子信号3: 动量趋势
        trend_mom = 0
        if mom_i > 10 and adx_i > 20:
            trend_mom = 1
        elif mom_i > 5:
            trend_mom = 0.5
        elif mom_i < -10:
            trend_mom = -1
        elif mom_i < -5:
            trend_mom = -0.5

        # 合成趋势复合评分（替代原先三因子独立加分）
        trend_composite = (trend_ma + trend_macd + trend_mom) / 3
        if trend_composite > 0.5:
            score += 3   # 强趋势共识
        elif trend_composite > 0:
            score += 1   # 弱/混合趋势
        elif trend_composite < -0.5:
            score -= 3   # 强空头共识
        elif trend_composite < 0:
            score -= 1   # 弱空头

        # ── 布林带 ──
        bbu = float(bb_upper.iloc[i]) if not pd.isna(bb_upper.iloc[i]) else 0
        bbl = float(bb_lower.iloc[i]) if not pd.isna(bb_lower.iloc[i]) else 0
        price_i = float(close.iloc[i])
        if bbl > 0 and price_i < bbl:
            score += 2
        elif bbu > 0 and price_i > bbu:
            score -= 2

        # ── ADX 趋势强度修正 ──
        if adx_i > 25:
            if score > 0:
                score += 1
            elif score < 0:
                score -= 1
        # Phase A1.1: ADX < 15 弱趋势削弱极端评分
        elif 0 < adx_i < 15:
            if score > 2:
                score -= 1
            elif score < -2:
                score += 1

        # ── 成交量信号 ──
        chg = float(change_pct.iloc[i]) if not pd.isna(change_pct.iloc[i]) else 0
        if vol_ratio > config_obj.VOLUME_SPIKE:
            if chg > 0:
                score += 2
            else:
                score -= 2

        # ── 量价背离 ──
        if i >= 10:
            recent_5_price = close.iloc[i - 4:i + 1].mean()
            prev_5_price = close.iloc[i - 9:i - 4].mean()
            recent_5_vol = volume.iloc[i - 4:i + 1].mean()
            prev_5_vol = volume.iloc[i - 9:i - 4].mean()
            if prev_5_vol > 0:
                price_up = recent_5_price > prev_5_price
                vol_shrink = recent_5_vol < prev_5_vol * 0.7
                if price_up and vol_shrink:
                    score -= 1
                price_down = recent_5_price < prev_5_price
                if price_down and vol_shrink:
                    score += 1

        # （动量因子已并入趋势复合评分，此处不再独立计分）

        # ── Phase A1.2: 大跌过滤 ──
        if chg < -5:
            if r < 40 and vol_ratio < 3:
                score += 1  # 缩量超卖反弹候选
            else:
                score -= 1  # 恐慌抛售

        # ── Phase A1.3: 市场状态惩罚 ──
        sig_date = (df.index[i].strftime("%Y-%m-%d")
                    if hasattr(df.index[i], "strftime")
                    else str(df.index[i]))
        if market_regime_map and sig_date in market_regime_map:
            if market_regime_map[sig_date] == "bearish" and score > 0:
                score -= 2

        # ── Phase B7: 周线偏向 ──
        if weekly_bias_map and sig_date in weekly_bias_map:
            wb = weekly_bias_map[sig_date]
            if wb < 0:
                score -= 1  # 周线空头减 1
            # 周线多头不加分（避免过度膨胀）

        # ── P2: 追高过滤 — 价格接近近期高点时降低评分 ──
        if i >= 20:
            high_20d = float(high.iloc[i-19:i+1].max())
            if high_20d > 0 and price_i > high_20d * 0.95:
                if score > 5:
                    score -= 2  # 已在20日高点附近，降低追高信号

        # ── 评分钳位 ──
        score = max(-10, min(10, score))

        daily_signals.append({
            "date": sig_date,
            "price": price_i,
            "rsi": round(r, 2),
            "score": score,
            "ticker": ticker,
            "momentum": round(mom_i, 2),
            "adx": round(adx_i, 2),
            "change_pct": round(chg, 2),
        })

    return daily_signals


def _calc_weekly_bias(df):
    """
    计算周线多空偏向，返回 {date_str: +1/0/-1}。
    周线 MA10 > MA30 → +1（多头），< MA30 → -1（空头），否则 0
    """
    if df is None or len(df) < 60:
        return {}

    weekly = resample_to_weekly(df)
    if len(weekly) < 12:
        return {}

    w_ma10 = weekly["Close"].rolling(10).mean()
    w_ma30 = weekly["Close"].rolling(30).mean()

    # 构建周日期 → 偏向映射
    weekly_bias = {}
    for i in range(len(weekly)):
        m10 = float(w_ma10.iloc[i]) if not pd.isna(w_ma10.iloc[i]) else 0
        m30 = float(w_ma30.iloc[i]) if not pd.isna(w_ma30.iloc[i]) else 0
        if m10 > 0 and m30 > 0:
            if m10 > m30:
                weekly_bias[weekly.index[i]] = 1
            elif m10 < m30:
                weekly_bias[weekly.index[i]] = -1
            else:
                weekly_bias[weekly.index[i]] = 0

    # 将日线日期映射到最近的周线偏向
    daily_bias = {}
    sorted_weeks = sorted(weekly_bias.keys())
    for i in range(len(df)):
        d = df.index[i]
        date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        # 找到 <= d 的最近一周
        bias = 0
        for w in reversed(sorted_weeks):
            if w <= d:
                bias = weekly_bias[w]
                break
        daily_bias[date_str] = bias

    return daily_bias


# ── 风险指标计算 ──────────────────────────────────────────────

def _calc_risk_metrics(daily_navs, trade_pnl_list):
    """
    计算风险指标。
    daily_navs: 每日组合净值列表 [(date, nav), ...]
    trade_pnl_list: 每笔卖出的收益率列表 [float, ...]
    """
    result = {
        "sharpe": 0, "calmar": 0, "max_drawdown_pct": 0,
        "annual_return_pct": 0, "annual_volatility_pct": 0,
    }

    # ── 从日净值序列计算 ──
    if len(daily_navs) >= 10:
        navs = np.array([n for _, n in daily_navs])
        daily_returns = np.diff(navs) / navs[:-1]

        # 年化
        trading_days = len(daily_returns)
        annual_factor = 252 / trading_days if trading_days > 0 else 1

        total_return = navs[-1] / navs[0] - 1
        annual_return = (1 + total_return) ** annual_factor - 1

        daily_std = np.std(daily_returns, ddof=1)
        annual_std = daily_std * math.sqrt(252)

        # Sharpe（无风险利率 3.5%/年）
        risk_free_daily = 0.035 / 252
        excess_daily = np.mean(daily_returns) - risk_free_daily
        sharpe = (excess_daily / daily_std * math.sqrt(252)
                  if daily_std > 0 else 0)

        # 最大回撤
        cumulative = np.cumprod(1 + daily_returns)
        cumulative = np.insert(cumulative, 0, 1.0)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - peak) / peak
        max_dd = float(np.min(drawdowns))

        # Calmar
        calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0

        result["sharpe"] = round(sharpe, 3)
        result["calmar"] = round(calmar, 3)
        result["max_drawdown_pct"] = round(max_dd * 100, 2)
        result["annual_return_pct"] = round(annual_return * 100, 2)
        result["annual_volatility_pct"] = round(annual_std * 100, 2)

    return result


# ── 交易执行 ──────────────────────────────────────────────────

def _bt_buy(portfolio, ticker, price_hkd, shares, date, reason, trades_log):
    """模拟买入，扣除现金，更新持仓"""
    slipped_price = price_hkd * (1 + SLIPPAGE_PCT / 100)
    cost_hkd = slipped_price * shares
    fee_hkd = calc_trade_fee_hkd(cost_hkd)
    total_cny = (cost_hkd + fee_hkd) * HKD_CNY_RATE

    if total_cny > portfolio["cash_cny"]:
        return False, 0

    portfolio["cash_cny"] -= total_cny

    if ticker in portfolio["positions"]:
        pos = portfolio["positions"][ticker]
        old_cost = pos["avg_cost_hkd"] * pos["shares"]
        new_cost = slipped_price * shares
        pos["shares"] += shares
        pos["avg_cost_hkd"] = (old_cost + new_cost) / pos["shares"]
        pos["total_cost_cny"] += total_cny
    else:
        portfolio["positions"][ticker] = {
            "shares": shares,
            "avg_cost_hkd": slipped_price,
            "total_cost_cny": total_cny,
            "lot_size": DEFAULT_LOT_SIZE,
            "high_watermark_hkd": slipped_price,
            "buy_date": date,
            "build_stage": 1,
            "tp_executed": [],
            "target_shares": 0,
        }

    portfolio["total_fees_cny"] = portfolio.get("total_fees_cny", 0) + fee_hkd * HKD_CNY_RATE

    trades_log.append({
        "date": date, "ticker": ticker, "action": "BUY",
        "price": price_hkd, "exec_price": round(slipped_price, 4),
        "shares": shares, "reason": reason,
    })
    return True, total_cny


def _bt_sell(portfolio, ticker, price_hkd, shares, date, reason, trades_log):
    """模拟卖出，增加现金，记录盈亏。返回 (pnl_pct, revenue_cny)"""
    pos = portfolio["positions"].get(ticker)
    if not pos or pos["shares"] <= 0:
        return 0, 0

    shares = min(shares, pos["shares"])
    slipped_price = price_hkd * (1 - SLIPPAGE_PCT / 100)
    revenue_hkd = slipped_price * shares
    fee_hkd = calc_trade_fee_hkd(revenue_hkd)
    net_cny = (revenue_hkd - fee_hkd) * HKD_CNY_RATE

    portfolio["cash_cny"] += net_cny
    portfolio["total_fees_cny"] = portfolio.get("total_fees_cny", 0) + fee_hkd * HKD_CNY_RATE

    # PnL 计算
    if pos["shares"] <= 0:
        return 0, 0
    cost_per_share_cny = pos["total_cost_cny"] / pos["shares"]
    sell_per_share_cny = net_cny / shares
    pnl_pct = (sell_per_share_cny / cost_per_share_cny - 1) * 100 if cost_per_share_cny > 0 else 0

    # 更新持仓
    pos["shares"] -= shares
    pos["total_cost_cny"] -= cost_per_share_cny * shares
    if pos["shares"] <= 0:
        del portfolio["positions"][ticker]

    trades_log.append({
        "date": date, "ticker": ticker, "action": "SELL",
        "price": price_hkd, "exec_price": round(slipped_price, 4),
        "shares": shares, "reason": reason,
        "pnl_pct": round(pnl_pct, 2),
    })
    return pnl_pct, net_cny


# ── 退出检查 ──────────────────────────────────────────────────

def _bt_check_exits(portfolio, ticker, price_hkd, score, date,
                    day_index, trades_log, trade_pnl_list, trading_dates):
    """
    按优先级检查所有退出条件。返回是否触发了全仓卖出。
    """
    pos = portfolio["positions"].get(ticker)
    if not pos or pos["shares"] <= 0:
        return False

    avg_cost = pos["avg_cost_hkd"]
    pnl_pct = (price_hkd / avg_cost - 1) * 100 if avg_cost > 0 else 0
    high_wm = pos.get("high_watermark_hkd", avg_cost)
    buy_date = pos.get("buy_date", date)

    # 计算持仓天数
    try:
        hold_days = (_dt.strptime(date, "%Y-%m-%d") -
                     _dt.strptime(buy_date, "%Y-%m-%d")).days
    except (ValueError, TypeError):
        hold_days = 0

    lot_size = pos.get("lot_size", DEFAULT_LOT_SIZE)

    # ── 1. 固定止损 ──
    if pnl_pct <= -config.STOP_LOSS_PCT * 100:
        pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                          date, f"止损({pnl_pct:.1f}%)", trades_log)
        trade_pnl_list.append(pnl)
        # 止损冷却
        if trading_dates:
            exp_idx = min(day_index + COOLDOWN_DAYS, len(trading_dates) - 1)
            portfolio["cooldown"][ticker] = trading_dates[exp_idx]
        return True

    # ── 2. 跟踪止损 ──
    if pnl_pct > 10:
        trail_stop = high_wm * 0.95
        if price_hkd <= trail_stop:
            pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                              date, f"跟踪止损(高点{high_wm:.2f},止损线{trail_stop:.2f})",
                              trades_log)
            trade_pnl_list.append(pnl)
            return True
    elif pnl_pct > 5:
        lock_stop = avg_cost * 1.02
        if price_hkd <= lock_stop:
            pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                              date, f"保本止损(盈利回撤至2%)", trades_log)
            trade_pnl_list.append(pnl)
            return True

    # ── 3. 全仓止盈 ──
    if pnl_pct >= config.TAKE_PROFIT_PCT * 100:
        pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                          date, f"全仓止盈({pnl_pct:.1f}%)", trades_log)
        trade_pnl_list.append(pnl)
        return True

    # ── 4. 分批止盈 ──
    tp_executed = pos.get("tp_executed", [])
    for level_idx, (tp_pct, tp_ratio) in enumerate(config.PARTIAL_TP_LEVELS):
        if level_idx in tp_executed:
            continue
        if pnl_pct >= tp_pct * 100:
            if tp_ratio >= 1.0:
                sell_shares = pos["shares"]
            else:
                sell_shares = int(pos["shares"] * tp_ratio)
                sell_shares = (sell_shares // lot_size) * lot_size
                if sell_shares <= 0:
                    sell_shares = lot_size
                sell_shares = min(sell_shares, pos["shares"])

            pnl, _ = _bt_sell(portfolio, ticker, price_hkd, sell_shares,
                              date, f"分批止盈L{level_idx+1}({tp_pct*100:.0f}%)",
                              trades_log)
            trade_pnl_list.append(pnl)
            # 更新已执行的止盈级别
            if ticker in portfolio["positions"]:
                portfolio["positions"][ticker].setdefault(
                    "tp_executed", []).append(level_idx)
            if tp_ratio >= 1.0 or ticker not in portfolio["positions"]:
                return True
            break  # 每日最多触发一级止盈

    # ── 5. 时间止损 ──
    if hold_days >= config.TIME_STOP_DAYS:
        if pnl_pct < config.TIME_STOP_MIN_GAIN_PCT:
            pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                              date, f"时间止损({hold_days}天,盈亏{pnl_pct:.1f}%)",
                              trades_log)
            trade_pnl_list.append(pnl)
            return True

    # ── 6. 评分卖出 ──
    if score <= -5:
        pnl, _ = _bt_sell(portfolio, ticker, price_hkd, pos["shares"],
                          date, f"强烈卖出信号(评分{score})", trades_log)
        trade_pnl_list.append(pnl)
        return True

    if score <= -4 and hold_days >= 5:
        sell_shares = (pos["shares"] // 2 // lot_size) * lot_size
        if sell_shares <= 0:
            sell_shares = pos["shares"]
        sell_shares = min(sell_shares, pos["shares"])
        pnl, _ = _bt_sell(portfolio, ticker, price_hkd, sell_shares,
                          date, f"弱信号减仓50%(评分{score},持{hold_days}天)",
                          trades_log)
        trade_pnl_list.append(pnl)
        if ticker not in portfolio["positions"]:
            return True

    if score <= -2 and hold_days >= 8 and pnl_pct < -5:
        if ticker in portfolio["positions"]:
            pos2 = portfolio["positions"][ticker]
            sell_shares = (pos2["shares"] // 2 // lot_size) * lot_size
            if sell_shares <= 0:
                sell_shares = pos2["shares"]
            sell_shares = min(sell_shares, pos2["shares"])
            pnl, _ = _bt_sell(portfolio, ticker, price_hkd, sell_shares,
                              date, f"亏损减仓(评分{score},盈亏{pnl_pct:.1f}%)",
                              trades_log)
            trade_pnl_list.append(pnl)

    return False


def _bt_check_drawdown(portfolio, daily_navs):
    """
    检查组合回撤，返回 (halt_buying, force_reduce)。
    """
    if len(daily_navs) < 2:
        return False, False

    peak_nav = max(n for _, n in daily_navs)
    current_nav = daily_navs[-1][1]
    dd = (current_nav - peak_nav) / peak_nav if peak_nav > 0 else 0

    halt_buying = dd <= -config.DRAWDOWN_HALT_PCT
    force_reduce = dd <= -config.DRAWDOWN_REDUCE_PCT

    return halt_buying, force_reduce


def _portfolio_nav(portfolio, price_map):
    """计算当前组合净值（CNY）"""
    nav = portfolio["cash_cny"]
    for ticker, pos in portfolio["positions"].items():
        if ticker in price_map:
            nav += pos["shares"] * price_map[ticker] * HKD_CNY_RATE
        else:
            nav += pos["total_cost_cny"]  # fallback: 用成本估算
    return nav


# ── 主回测入口 ────────────────────────────────────────────────

def run_backtest(mode="full", days=90, window_size=7, tickers=None):
    """
    统一回测入口
    mode: "full"=完整回测, "weekly"=最近一周, "multiwindow"=多区间
    """
    if tickers is None:
        from hkstock.strategy.screener import get_dynamic_watchlist
        tickers, _ = get_dynamic_watchlist(top_n=100)
        if not tickers:
            raise RuntimeError("动态筛选未返回任何股票，回测终止")

    print(f"\n{'='*60}")
    print(f"回测模式: {mode}  |  股票数: {len(tickers)}  |  历史天数: {days}")
    print(f"⚠️ 生存者偏差警告: 回测池来自当前活跃股票，已退市股票未纳入")
    print(f"  汇率: {HKD_CNY_RATE} | 滑点: {SLIPPAGE_PCT}% (单边)")
    print(f"{'='*60}\n")

    if mode == "weekly":
        return _run_weekly(tickers, days=days)
    elif mode == "multiwindow":
        return _run_multiwindow(tickers, days=days, window_size=window_size)
    else:
        return _run_full(tickers, days=days)


def _run_full(tickers, days=90):
    """
    完整组合级回测：
    - 日级别逐日模拟，跨所有标的统一管理资金
    - 含全部风控逻辑（止损/跟踪止损/时间止损/分批止盈/评分卖出）
    - 组合回撤保护 + 市场状态机 + 冷却期 + 连亏熔断
    """
    # ── 初始化 ──
    portfolio = {
        "cash_cny": config.TOTAL_CAPITAL,
        "positions": {},
        "total_capital_cny": config.TOTAL_CAPITAL,
        "total_fees_cny": 0,
        "cooldown": {},           # ticker -> expiry_date
        "consecutive_losses": 0,
        "circuit_breaker_until": None,
    }

    trades_log = []
    trade_pnl_list = []
    daily_navs = []

    # Phase D: 交易频率控制
    weekly_new_positions = {}  # {iso_week_str: count} 每周新开仓计数

    # ── 计算市场状态 ──
    print("  正在计算恒指市场状态...", end="", flush=True)
    regime_map = _calc_market_regime(days)
    print(f" 完成（{len(regime_map)} 天）")

    # ── 获取数据 & 计算信号 ──
    print("  正在获取历史数据...", end="", flush=True)
    all_signals = {}     # ticker -> [signal_dicts]
    all_dataframes = {}  # ticker -> DataFrame（用于周线计算）
    for ticker in tickers:
        df = fetch_history(ticker, days=days)
        if df is not None and len(df) >= 30:
            all_dataframes[ticker] = df
            # 计算周线偏向
            weekly_bias = _calc_weekly_bias(df)
            signals = backtest_analyze(
                df, ticker, config,
                market_regime_map=regime_map,
                weekly_bias_map=weekly_bias,
            )
            if signals:
                all_signals[ticker] = signals
    print(f" 完成（{len(all_signals)} 只有效）")

    if not all_signals:
        print("  ⚠️ 无有效数据，回测终止")
        return {"mode": "full", "summary": {}, "trades": []}

    # ── 构建每日时间轴 ──
    # {date_str: {ticker: signal_dict}}
    date_ticker_map = {}
    for ticker, signals in all_signals.items():
        for sig in signals:
            d = sig["date"]
            if d not in date_ticker_map:
                date_ticker_map[d] = {}
            date_ticker_map[d][ticker] = sig

    trading_dates = sorted(date_ticker_map.keys())
    print(f"  回测时间轴: {trading_dates[0]} ~ {trading_dates[-1]} ({len(trading_dates)} 天)")

    # ── 逐日模拟 ──
    prev_nav = config.TOTAL_CAPITAL

    for day_idx, date in enumerate(trading_dates):
        today_signals = date_ticker_map[date]

        # 获取当日所有价格
        price_map = {t: sig["price"] for t, sig in today_signals.items()}

        # 补充已持仓但今日无信号的股票价格（用上一次已知价格）
        for ticker in list(portfolio["positions"].keys()):
            if ticker not in price_map:
                # 在历史信号中找最近价格
                if ticker in all_signals:
                    for sig in reversed(all_signals[ticker]):
                        if sig["date"] <= date:
                            price_map[ticker] = sig["price"]
                            break

        # ── Step 0: 更新高水位 ──
        for ticker, pos in portfolio["positions"].items():
            if ticker in price_map:
                cur_price = price_map[ticker]
                if cur_price > pos.get("high_watermark_hkd", 0):
                    pos["high_watermark_hkd"] = cur_price

        # ── Step 1: 退出检查 ──
        for ticker in list(portfolio["positions"].keys()):
            if ticker not in price_map:
                continue
            cur_price = price_map[ticker]
            cur_score = today_signals[ticker]["score"] if ticker in today_signals else 0
            _bt_check_exits(
                portfolio, ticker, cur_price, cur_score, date,
                day_idx, trades_log, trade_pnl_list, trading_dates,
            )

        # ── 连亏熔断追踪 ──
        # 在卖出后检查连败
        recent_sells = [t for t in trades_log if t["action"] == "SELL" and t["date"] == date]
        for sell_trade in recent_sells:
            if sell_trade.get("pnl_pct", 0) < 0:
                portfolio["consecutive_losses"] += 1
            else:
                portfolio["consecutive_losses"] = 0

        if portfolio["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
            exp_idx = min(day_idx + CIRCUIT_BREAKER_DAYS, len(trading_dates) - 1)
            portfolio["circuit_breaker_until"] = trading_dates[exp_idx]
            portfolio["consecutive_losses"] = 0  # 重置

        # ── Step 2: 组合回撤检查 ──
        current_nav = _portfolio_nav(portfolio, price_map)
        daily_navs.append((date, current_nav))

        halt_buying, force_reduce = _bt_check_drawdown(portfolio, daily_navs)

        # 强制减仓
        if force_reduce:
            for ticker in list(portfolio["positions"].keys()):
                if ticker not in price_map:
                    continue
                pos = portfolio["positions"][ticker]
                lot_size = pos.get("lot_size", DEFAULT_LOT_SIZE)
                sell_shares = (pos["shares"] // 2 // lot_size) * lot_size
                if sell_shares > 0:
                    pnl, _ = _bt_sell(portfolio, ticker, price_map[ticker],
                                      sell_shares, date, "回撤强制减仓50%",
                                      trades_log)
                    trade_pnl_list.append(pnl)
            halt_buying = True  # 减仓后也暂停买入

        # ── 单日亏损检查 ──
        daily_loss = (current_nav - prev_nav) / prev_nav if prev_nav > 0 else 0
        if daily_loss < -DAILY_LOSS_LIMIT_PCT:
            halt_buying = True  # 单日亏损超限，暂停买入

        # ── 熔断检查 ──
        if (portfolio["circuit_breaker_until"]
                and date <= portfolio["circuit_breaker_until"]):
            halt_buying = True

        # ── Step 3a: 分批建仓续建 ──
        if not halt_buying:
            for ticker in list(portfolio["positions"].keys()):
                pos = portfolio["positions"].get(ticker)
                if not pos or pos.get("build_stage", 3) >= 3:
                    continue
                if ticker not in today_signals:
                    continue
                sig = today_signals[ticker]

                # P1: 分批建仓间隔检查（至少5个交易日）
                buy_date = pos.get("buy_date", "")
                if buy_date:
                    try:
                        days_since_buy = (_dt.strptime(date, "%Y-%m-%d") - _dt.strptime(buy_date, "%Y-%m-%d")).days
                        min_interval = 5 * pos.get("build_stage", 1)  # 第2批>=5天, 第3批>=10天
                        if days_since_buy < min_interval:
                            continue
                    except (ValueError, TypeError):
                        pass

                # P1: 价格确认 — 后续批次需要价格高于均价（趋势有效确认）
                if sig["price"] < pos.get("avg_cost_hkd", 0) * 0.98:
                    continue  # 价格低于成本98%，不加仓

                if sig["score"] < 0:
                    # 评分转负，取消后续建仓
                    pos["build_stage"] = 3
                    continue

                target = pos.get("target_shares", 0)
                if target <= 0:
                    continue

                stage = pos["build_stage"]
                ratios = config.PARTIAL_BUILD_RATIOS
                if stage < len(ratios):
                    batch_shares = int(target * ratios[stage])
                    batch_shares = (batch_shares // DEFAULT_LOT_SIZE) * DEFAULT_LOT_SIZE
                    if batch_shares <= 0:
                        batch_shares = DEFAULT_LOT_SIZE

                    available = portfolio["cash_cny"] - config.RESERVE_CASH
                    batch_cost = batch_shares * sig["price"] * HKD_CNY_RATE * 1.01
                    if available >= batch_cost and batch_cost > 0:
                        ok, _ = _bt_buy(portfolio, ticker, sig["price"],
                                        batch_shares, date,
                                        f"分批建仓{stage+1}/{len(ratios)}",
                                        trades_log)
                        if ok:
                            pos["build_stage"] = stage + 1

        # ── Step 3: 新买入 ──
        if not halt_buying:
            # Phase D2: 市场状态机动态调节（门槛提高）
            regime = regime_map.get(date, "neutral")
            if regime == "bullish":
                buy_threshold = 6
                position_scale = 1.0
            elif regime == "neutral":
                buy_threshold = 7
                position_scale = 0.5
            else:  # bearish
                buy_threshold = 8
                position_scale = 0.25

            # Phase D1: 每周新开仓上限检查
            try:
                iso_week = _dt.strptime(date, "%Y-%m-%d").strftime("%Y-W%W")
            except (ValueError, TypeError):
                iso_week = date[:7]
            week_count = weekly_new_positions.get(iso_week, 0)
            if week_count >= MAX_NEW_POSITIONS_PER_WEEK:
                pass  # 本周已达上限，跳过新开仓
            else:
                # Phase D4: 持仓健康度检查
                allow_new_entry = True
                if len(portfolio["positions"]) >= 3:
                    pos_pnls = []
                    for t_ticker, t_pos in portfolio["positions"].items():
                        if t_ticker in price_map:
                            t_pnl = (price_map[t_ticker] / t_pos["avg_cost_hkd"] - 1) * 100
                            pos_pnls.append(t_pnl)
                    if pos_pnls:
                        avg_pos_pnl = sum(pos_pnls) / len(pos_pnls)
                        all_losing = all(p < 0 for p in pos_pnls)
                        if all_losing and len(pos_pnls) >= 3:
                            allow_new_entry = False  # 全部亏损，暂停新开
                        elif len(portfolio["positions"]) >= 5 and avg_pos_pnl > 3:
                            allow_new_entry = False  # >=5只且均盈>3%，让赢家跑

                if allow_new_entry:
                    candidates = []
                    for ticker, sig in today_signals.items():
                        if sig["score"] >= buy_threshold and ticker not in portfolio["positions"]:
                            candidates.append((ticker, sig))
                    candidates.sort(key=lambda x: x[1]["score"], reverse=True)

                    for ticker, sig in candidates:
                        # D1: 再次检查周上限
                        if weekly_new_positions.get(iso_week, 0) >= MAX_NEW_POSITIONS_PER_WEEK:
                            break
                        if len(portfolio["positions"]) >= config.MAX_POSITIONS:
                            break
                        available = portfolio["cash_cny"] - config.RESERVE_CASH
                        if available < MIN_OPEN_AMOUNT_CNY:
                            break
                        if ticker in portfolio["cooldown"]:
                            if date <= portfolio["cooldown"][ticker]:
                                continue
                            else:
                                del portfolio["cooldown"][ticker]
                        sector = get_sector(ticker)
                        sector_count = sum(
                            1 for t in portfolio["positions"]
                            if get_sector(t) == sector
                        )
                        if sector_count >= MAX_PER_SECTOR:
                            continue
                        max_pos_cny = config.MAX_POSITION * position_scale
                        target_cny = min(max_pos_cny, available)
                        target_shares_total = int(target_cny / (sig["price"] * HKD_CNY_RATE))
                        target_shares_total = (target_shares_total // DEFAULT_LOT_SIZE) * DEFAULT_LOT_SIZE
                        if target_shares_total <= 0:
                            continue
                        first_batch = int(target_shares_total * config.PARTIAL_BUILD_RATIOS[0])
                        first_batch = (first_batch // DEFAULT_LOT_SIZE) * DEFAULT_LOT_SIZE
                        if first_batch <= 0:
                            first_batch = DEFAULT_LOT_SIZE
                        # D3: 最低开仓金额检查
                        batch_cost = first_batch * sig["price"] * HKD_CNY_RATE * 1.01
                        if batch_cost < MIN_OPEN_AMOUNT_CNY:
                            continue  # 金额太小，赚不回手续费
                        if batch_cost > available:
                            continue
                        ok, _ = _bt_buy(portfolio, ticker, sig["price"],
                                        first_batch, date,
                                        f"买入(评分{sig['score']},状态{regime})",
                                        trades_log)
                        if ok and ticker in portfolio["positions"]:
                            portfolio["positions"][ticker]["target_shares"] = target_shares_total
                            portfolio["positions"][ticker]["build_stage"] = 1
                            # D1: 更新周计数
                            weekly_new_positions[iso_week] = weekly_new_positions.get(iso_week, 0) + 1

        # ── 更新前日 NAV ──
        prev_nav = current_nav

    # ── 统计 ──
    sells = [t for t in trades_log if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]
    pnl_list = [t.get("pnl_pct", 0) for t in sells]

    # 最终净值（含持仓按最后价格估值）
    last_price_map = {}
    if trading_dates:
        last_date = trading_dates[-1]
        if last_date in date_ticker_map:
            for t, sig in date_ticker_map[last_date].items():
                last_price_map[t] = sig["price"]
    final_nav = _portfolio_nav(portfolio, last_price_map)
    total_return_pct = (final_nav / config.TOTAL_CAPITAL - 1) * 100

    risk_metrics = _calc_risk_metrics(daily_navs, pnl_list)
    win_rate = round(len(wins) / max(1, len(sells)) * 100, 1)
    avg_pnl = round(sum(pnl_list) / max(1, len(sells)), 2)

    results = {
        "mode": "full",
        "tickers": list(all_signals.keys()),
        "summary": {
            "total_trades": len(trades_log),
            "buy_trades": len([t for t in trades_log if t["action"] == "BUY"]),
            "sell_trades": len(sells),
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": round(sum(pnl_list), 2),
            "max_single_win": round(max(pnl_list) if pnl_list else 0, 2),
            "max_single_loss": round(min(pnl_list) if pnl_list else 0, 2),
            "starting_capital": config.TOTAL_CAPITAL,
            "ending_nav": round(final_nav, 2),
            "total_return_pct": round(total_return_pct, 2),
            "positions_held_at_end": len(portfolio["positions"]),
            "total_fees_cny": round(portfolio.get("total_fees_cny", 0), 2),
            **risk_metrics,
            "survivorship_bias_warning": True,
            "adjusted_win_rate": round(win_rate * SURVIVORSHIP_BIAS_DISCOUNT, 1),
            "adjusted_avg_pnl": round(avg_pnl * SURVIVORSHIP_BIAS_DISCOUNT, 2),
            "adjusted_total_return_pct": round(
                total_return_pct * SURVIVORSHIP_BIAS_DISCOUNT, 2),
        },
        "trades": trades_log,
        "daily_navs": daily_navs,
    }

    # ── 打印结果 ──
    s = results["summary"]
    print(f"\n{'='*60}")
    print(f"回测结果")
    print(f"{'='*60}")
    print(f"  起始资金: {s['starting_capital']:,.0f} CNY")
    print(f"  最终净值: {s['ending_nav']:,.0f} CNY")
    print(f"  总收益率: {s['total_return_pct']:+.2f}%")
    print(f"  未平仓数: {s['positions_held_at_end']} 只")
    print(f"  总费用:   {s['total_fees_cny']:,.0f} CNY")
    print(f"\n  交易统计:")
    print(f"    买入次数: {s['buy_trades']}")
    print(f"    卖出次数: {s['sell_trades']}")
    print(f"    胜率: {s['win_rate']}%")
    print(f"    平均盈亏: {s['avg_pnl']}%")
    print(f"    累计盈亏: {s['total_pnl']}%")
    print(f"    最大单笔盈利: +{s['max_single_win']}%")
    print(f"    最大单笔亏损: {s['max_single_loss']}%")
    print(f"\n  风险指标:")
    print(f"    Sharpe Ratio:  {s['sharpe']}")
    print(f"    Calmar Ratio:  {s['calmar']}")
    print(f"    最大回撤:       {s['max_drawdown_pct']}%")
    print(f"    年化收益:       {s['annual_return_pct']}%")
    print(f"    年化波动:       {s.get('annual_volatility_pct', 0)}%")
    print(f"\n  {'!'*50}")
    print(f"  ⚠️  生存者偏差调整（折扣 {int((1-SURVIVORSHIP_BIAS_DISCOUNT)*100)}%）")
    print(f"    调整后胜率:     {s['adjusted_win_rate']}%")
    print(f"    调整后平均盈亏: {s['adjusted_avg_pnl']}%")
    print(f"    调整后总收益:   {s['adjusted_total_return_pct']}%")
    print(f"  {'!'*50}")

    return results


def _run_weekly(tickers, days=30):
    """一周回测：仅生成信号，不模拟交易"""
    results = {"mode": "weekly", "tickers": [], "daily_pnl": []}

    regime_map = _calc_market_regime(days)

    for ticker in tickers:
        df = fetch_history(ticker, days=days)
        signals = backtest_analyze(df, ticker, config,
                                   market_regime_map=regime_map)
        if signals:
            results["tickers"].append(ticker)
            recent = signals[-7:] if len(signals) >= 7 else signals
            for sig in recent:
                results["daily_pnl"].append(sig)

    print(f"\n一周回测: 分析 {len(results['tickers'])} 只股票，"
          f"{len(results['daily_pnl'])} 条信号")
    return results


def _run_multiwindow(tickers, days=90, window_size=7):
    """多区间回测（防过拟合）：对每只股票分窗口统计信号分布"""
    results = {"mode": "multiwindow", "windows": []}

    regime_map = _calc_market_regime(days)

    for ticker in tickers[:10]:
        df = fetch_history(ticker, days=days)
        signals = backtest_analyze(df, ticker, config,
                                   market_regime_map=regime_map)
        if not signals or len(signals) < window_size * 4:
            continue

        step = len(signals) // 4
        for w in range(4):
            window = signals[w * step: (w + 1) * step]
            buys = sum(1 for s in window if s["score"] >= 7)
            sells_count = sum(1 for s in window if s["score"] <= -3)
            avg_score = round(sum(s["score"] for s in window) / len(window), 2)
            results["windows"].append({
                "ticker": ticker,
                "window": w + 1,
                "start": window[0]["date"],
                "end": window[-1]["date"],
                "buy_signals": buys,
                "sell_signals": sells_count,
                "avg_score": avg_score,
            })

    print(f"\n多区间回测: {len(results['windows'])} 个窗口")
    for w in results["windows"]:
        print(f"  {w['ticker']} 窗口{w['window']}: {w['start']}~{w['end']}  "
              f"买{w['buy_signals']}次 卖{w['sell_signals']}次 "
              f"均分{w['avg_score']}")

    return results


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="HK Stock Backtest")
    _parser.add_argument("mode", nargs="?", default="full",
                         choices=["full", "weekly", "multiwindow"],
                         help="回测模式 (default: full)")
    _parser.add_argument("--days", type=int, default=90,
                         help="回测历史天数 (default: 90)")
    _args = _parser.parse_args()
    run_backtest(mode=_args.mode, days=_args.days)
