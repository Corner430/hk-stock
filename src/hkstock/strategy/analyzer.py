"""
港股数据抓取与技术分析模块（使用腾讯股票接口，真实数据）
"""
import pandas as pd
from datetime import datetime
import json
import os
import time
from hkstock.data.real_data import fetch_history, fetch_realtime
from hkstock.analysis.indicators import calc_rsi, calc_macd, calc_bollinger, calc_adx, calc_atr, calc_momentum
from hkstock.analysis.scoring import clamp_score, score_to_action, score_to_position_pct
from hkstock.trading.position_manager import get_hkd_to_cny as _get_rate

# 股票名称缓存（由动态筛选填充）
NAME_CACHE = {}

def fetch_stock_data(ticker, days=90):
    """抓取单只股票历史数据（腾讯真实数据源）"""
    return fetch_history(ticker, days=days)

def analyze_stock(ticker, config):
    """综合分析单只股票，返回信号"""
    df = fetch_stock_data(ticker)
    if df is None or len(df) < 15:
        data_len = len(df) if df is not None else 0
        print(f"  [SKIP] {ticker} 数据不足（{data_len}天 < 15天最低要求）")
        return None

    close = df["Close"]
    volume = df["Volume"]

    # 计算指标
    rsi = calc_rsi(close, config.RSI_PERIOD)
    ma_short = close.rolling(config.MA_SHORT).mean()
    ma_long = close.rolling(config.MA_LONG).mean()
    macd, signal_line, histogram = calc_macd(close)
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)
    avg_volume = volume.rolling(20).mean()

    # ADX 趋势强度
    if "High" in df.columns and "Low" in df.columns:
        adx, plus_di, minus_di = calc_adx(df["High"], df["Low"], close)
        latest_adx = None  # 临时存储，safe_float 定义后再转换
    else:
        latest_adx = 0

    def safe_float(val, default=0):
        try:
            v = float(val)
            return v if not (v != v) else default  # NaN check
        except (TypeError, ValueError):
            return default

    # 转换 ADX 值
    if latest_adx is None:
        latest_adx = safe_float(adx.iloc[-1], 0)

    # 最新值
    latest = {
        "ticker": ticker,
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "price": round(safe_float(close.iloc[-1]), 3),
        "prev_close": round(safe_float(close.iloc[-2]), 3),
        "change_pct": round((safe_float(close.iloc[-1]) / safe_float(close.iloc[-2], 1) - 1) * 100, 2),
        "volume": int(safe_float(volume.iloc[-1])),
        "avg_volume": int(safe_float(avg_volume.iloc[-1])),
        "rsi": round(safe_float(rsi.iloc[-1], 50), 2),
        "ma_short": round(safe_float(ma_short.iloc[-1]), 3),
        "ma_long": round(safe_float(ma_long.iloc[-1]), 3),
        "macd": round(safe_float(macd.iloc[-1]), 4),
        "macd_signal": round(safe_float(signal_line.iloc[-1]), 4),
        "bb_upper": round(safe_float(bb_upper.iloc[-1]), 3),
        "bb_lower": round(safe_float(bb_lower.iloc[-1]), 3),
        "adx": round(latest_adx, 2),
    }

    # 如果 ChangePercent 列存在，直接用
    if "ChangePercent" in df.columns:
        latest["change_pct"] = round(safe_float(df["ChangePercent"].iloc[-1]), 2)

    # 成交量倍数
    if latest["avg_volume"] > 0:
        latest["volume_ratio"] = round(latest["volume"] / latest["avg_volume"], 2)
    else:
        latest["volume_ratio"] = 1.0

    # 信号评分（-10 到 +10）
    score = 0
    signals = []

    # RSI 渐进式评分（取消一票否决，改为分级减分）
    rsi = latest["rsi"]
    if rsi < config.RSI_OVERSOLD:
        score += 3
        signals.append(f"RSI={rsi}（超卖区间，买入信号）")
    elif rsi > 80:
        score -= 4
        signals.append(f"RSI={rsi}（严重超买，强烈卖出信号）")
    elif rsi > config.RSI_OVERBOUGHT:
        score -= 2
        signals.append(f"RSI={rsi}（超买区间，注意回调风险）")

    # 均线金叉/死叉
    if latest["ma_short"] > latest["ma_long"] and latest["ma_short"] > 0 and latest["ma_long"] > 0:
        score += 2
        signals.append("均线金叉（短期均线上穿长期，看涨）")
    elif latest["ma_short"] < latest["ma_long"] and latest["ma_short"] > 0:
        score -= 2
        signals.append("均线死叉（短期均线下穿长期，看跌）")

    # MACD 信号
    if latest["macd"] > latest["macd_signal"] and latest["macd"] > 0:
        score += 2
        signals.append("MACD 金叉且在零轴上方（强势买入）")
    elif latest["macd"] > latest["macd_signal"]:
        score += 1
        signals.append("MACD 金叉（温和买入）")
    elif latest["macd"] < latest["macd_signal"]:
        score -= 2
        signals.append("MACD 死叉（卖出信号）")

    # 布林带信号
    if latest["bb_lower"] > 0 and latest["price"] < latest["bb_lower"]:
        score += 2
        signals.append("价格触及布林带下轨（超卖，潜在反弹）")
    elif latest["bb_upper"] > 0 and latest["price"] > latest["bb_upper"]:
        score -= 2
        signals.append("价格触及布林带上轨（超买，注意回调）")

    # ADX 趋势强度信号
    if latest.get("adx", 0) > 25:
        # 强趋势中，顺势信号加分
        if score > 0:
            score += 1
            signals.append(f"ADX={latest['adx']}（强趋势，顺势信号增强）")
        elif score < 0:
            score -= 1
            signals.append(f"ADX={latest['adx']}（强趋势，逆势风险加大）")
    elif latest.get("adx", 0) < 15 and latest.get("adx", 0) > 0:
        # 弱趋势中，信号可靠性降低
        if score > 2:
            score -= 1
            signals.append(f"ADX={latest['adx']}（趋势弱，信号可靠性降低，评分-1）")
        elif score < -2:
            score += 1
            signals.append(f"ADX={latest['adx']}（趋势弱，信号可靠性降低，评分+1）")
        else:
            signals.append(f"ADX={latest['adx']}（趋势较弱，震荡市）")

    # 成交量信号（增强权重 + 量价背离检测）
    if latest["volume_ratio"] > config.VOLUME_SPIKE:
        if latest["change_pct"] > 0:
            score += 2
            signals.append(f"放量上涨（成交量{latest['volume_ratio']}x，资金流入，权重增强）")
        else:
            score -= 2
            signals.append(f"放量下跌（成交量{latest['volume_ratio']}x，资金流出，权重增强）")

    # 量价背离检测（价格创新高但成交量萎缩 or 价格创新低但成交量萎缩）
    if len(df) >= 10:
        recent_5 = close.iloc[-5:]
        recent_vol_5 = volume.iloc[-5:]
        prev_5 = close.iloc[-10:-5]
        prev_vol_5 = volume.iloc[-10:-5]
        price_up = recent_5.mean() > prev_5.mean()
        vol_down = recent_vol_5.mean() < prev_vol_5.mean() * 0.7
        if price_up and vol_down:
            score -= 1
            signals.append("量价背离（价格上涨但成交量萎缩，上涨动能不足）")
        price_down = recent_5.mean() < prev_5.mean()
        vol_down_2 = recent_vol_5.mean() < prev_vol_5.mean() * 0.7
        if price_down and vol_down_2:
            score += 1
            signals.append("缩量下跌（抛压减弱，可能接近底部）")

    # 动量因子
    if len(df) >= config.MOMENTUM_PERIOD + 1:
        momentum_val = calc_momentum(close, period=config.MOMENTUM_PERIOD)
        latest_momentum = safe_float(momentum_val.iloc[-1], 0)
        latest["momentum"] = round(latest_momentum, 2)
        if latest_momentum > 10:
            score += 2
            signals.append(f"强劲动量+{latest_momentum:.1f}%（{config.MOMENTUM_PERIOD}日涨幅显著）")
        elif latest_momentum > 5:
            score += 1
            signals.append(f"正向动量+{latest_momentum:.1f}%")
        elif latest_momentum < -10:
            score -= 2
            signals.append(f"严重弱势动量{latest_momentum:.1f}%（{config.MOMENTUM_PERIOD}日跌幅大，权重增强）")
        elif latest_momentum < -5:
            score -= 1
            signals.append(f"弱势动量{latest_momentum:.1f}%（{config.MOMENTUM_PERIOD}日跌幅明显）")

    # 跌幅超大时区分技术性超跌和利空恐慌
    if latest["change_pct"] < -5:
        if latest["rsi"] < 40 and latest["volume_ratio"] < 3:
            score += 1
            signals.append(f"单日跌幅{latest['change_pct']}%+RSI超卖区间（可能超跌反弹）")
        else:
            score -= 1
            signals.append(f"单日跌幅{latest['change_pct']}%（放量暴跌，利空恐慌，避开）")

    # 生成建议（三档买入：强烈买入 / 买入 / 试探性买入）
    action = score_to_action(score)

    # 评分钳位到 [-10, +10]，防止多因子叠加导致评分通胀
    score = clamp_score(score)

    latest["score"] = score
    latest["action"] = action
    latest["signals"] = signals

    # ATR-based position sizing
    atr_position_cny = None
    if "High" in df.columns and "Low" in df.columns:
        atr_series = calc_atr(df["High"], df["Low"], close, config.ATR_PERIOD)
        latest_atr = safe_float(atr_series.iloc[-1], 0)
        latest["atr"] = round(latest_atr, 4)
        if latest_atr > 0 and latest["price"] > 0:
            # 每笔风险 = 总资金 * ATR_RISK_PER_TRADE
            risk_per_trade = config.TOTAL_CAPITAL * config.ATR_RISK_PER_TRADE
            # 止损距离 = ATR * multiplier (HKD)
            stop_distance_hkd = latest_atr * config.ATR_MULTIPLIER
            # 可买股数 = risk / stop_distance / rate
            rate = _get_rate()
            shares_by_risk = risk_per_trade / (stop_distance_hkd * rate)
            atr_position_cny = shares_by_risk * latest["price"] * rate

    # 建议仓位（三档对应不同仓位）
    if score >= 6:
        suggested_position_pct = 0.15    # 强烈买入：15%
    elif score >= 4:
        suggested_position_pct = 0.10    # 买入：10%
    elif score >= 3:
        suggested_position_pct = 0.05    # 试探性买入：5%
    else:
        suggested_position_pct = 0

    pct_position_cny = int(config.TOTAL_CAPITAL * suggested_position_pct)
    # 取 ATR 仓位和百分比仓位的较小值（风控优先）
    if atr_position_cny is not None and pct_position_cny > 0:
        latest["suggested_position_cny"] = min(pct_position_cny, int(atr_position_cny))
    else:
        latest["suggested_position_cny"] = pct_position_cny
    latest["name"] = NAME_CACHE.get(ticker, ticker)

    return latest

def run_analysis(config, use_dynamic=True):
    """对股票进行分析：严格使用动态筛选，不使用预设列表"""

    from hkstock.strategy.screener import get_dynamic_watchlist
    watchlist, name_cache_extra = get_dynamic_watchlist(top_n=100)
    NAME_CACHE.update(name_cache_extra)
    if not watchlist:
        raise RuntimeError("动态筛选未返回任何股票，分析终止")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 动态筛选出 {len(watchlist)} 只活跃股票")

    # ── 注入 IPO 新股（绕过筛选器历史数据门槛）──────────────
    try:
        from hkstock.strategy.ipo_tracker import get_ipo_tickers_for_analysis
        ipo_tickers, ipo_names = get_ipo_tickers_for_analysis(min_days=15)
        existing = set(watchlist)
        new_ipo = [t for t in ipo_tickers if t not in existing]
        if new_ipo:
            watchlist.extend(new_ipo)
            NAME_CACHE.update(ipo_names)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] +{len(new_ipo)} 只 IPO 新股注入分析管道")
    except Exception as e:
        print(f"[WARN] IPO 新股注入失败（不影响主流程）: {e}")

    # ── 大盘趋势过滤（恒生指数 + 市场数据模块） ──────────
    market_regime = "neutral"  # neutral / bullish / bearish
    market_signals = None
    position_multiplier = 1.0
    try:
        hsi_df = fetch_stock_data("HSI.HI", days=60)
        if hsi_df is not None and len(hsi_df) >= 30:
            from hkstock.analysis.indicators import calc_rsi
            hsi_close = hsi_df["Close"]
            hsi_ma10 = hsi_close.rolling(10).mean()
            hsi_ma30 = hsi_close.rolling(30).mean()
            hsi_rsi = calc_rsi(hsi_close, 14)
            latest_ma10 = float(hsi_ma10.iloc[-1])
            latest_ma30 = float(hsi_ma30.iloc[-1])
            latest_rsi = float(hsi_rsi.iloc[-1])

            if latest_ma10 > latest_ma30 and latest_rsi > 50:
                market_regime = "bullish"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📈 大盘趋势：偏多（恒指MA10>MA30, RSI={latest_rsi:.0f}）")
            elif latest_ma10 < latest_ma30 and latest_rsi < 45:
                market_regime = "bearish"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📉 大盘趋势：偏空（恒指MA10<MA30, RSI={latest_rsi:.0f}）")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ 大盘趋势：中性（恒指RSI={latest_rsi:.0f}）")
    except Exception as e:
        print(f"[WARN] 恒生指数趋势检测失败: {e}")

    # 获取市场级数据（南向资金、AH溢价、VHSI等），用于仓位倍数调节
    try:
        from hkstock.data.market_data import get_market_signals
        market_signals = get_market_signals()
        if market_signals.get("valid"):
            position_multiplier = market_signals.get("position_multiplier", 1.0)
            ms_sentiment = market_signals.get("overall_sentiment", "neutral")
            ms_score = market_signals.get("score", 0)
            sb_info = market_signals.get("southbound", {})
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 市场信号: "
                  f"情绪={ms_sentiment}(分数{ms_score:+d}) "
                  f"仓位倍数={position_multiplier:.1f} "
                  f"南向资金={sb_info.get('net_buy', '?')}亿")
    except Exception as e:
        print(f"[WARN] 市场数据获取失败（不影响主流程）: {e}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始分析 {len(watchlist)} 只股票...")
    results = []
    for ticker in watchlist:
        print(f"  分析 {ticker} {NAME_CACHE.get(ticker, '')}...")
        result = analyze_stock(ticker, config)
        if result:
            results.append(result)

    # ── 基本面增强（第一期）──────────────────────────────
    try:
        from hkstock.analysis.fundamentals import enrich_with_fundamentals
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始基本面过滤 + 港交所公告检查...")
        enriched = []
        for r in results:
            if r.get("score") == -99:   # 已被技术面排除，跳过
                enriched.append(r)
                continue
            r2 = enrich_with_fundamentals(r)
            enriched.append(r2)
            time.sleep(0.05)   # 轻微限速
        results = enriched
        filtered = len([r for r in results if r.get("action") == "基本面排除"])
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 基本面过滤完成，排除 {filtered} 只")
    except Exception as e:
        print(f"[WARN] 基本面增强失败（不影响主流程）: {e}")

    # ── 板块热度加分/减分 ──────────────────────────────
    output_sector_report = ""
    try:
        from hkstock.analysis.sector import fetch_sector_performance, get_hot_sectors, get_cold_sectors, sector_score_boost, get_sector_report
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 检测板块热度...")
        sector_perf = fetch_sector_performance()
        hot_sectors = get_hot_sectors(sector_perf)
        cold_sectors = get_cold_sectors(sector_perf)
        if hot_sectors:
            print(f"  🔥 今日热门板块：{'、'.join(hot_sectors)}")
        if cold_sectors:
            print(f"  ❄️ 今日冷门板块：{'、'.join(cold_sectors)}")
        for r in results:
            if r.get("score", 0) == -99:
                continue
            boost = sector_score_boost(r["ticker"], hot_sectors, r.get("name", ""),
                                       sector_perf=sector_perf, cold_sectors=cold_sectors)
            if boost != 0:
                r["score"] = r.get("score", 0) + boost
                if boost > 0:
                    r.setdefault("signals", []).append(f"板块热度加分+{boost}（所属板块为今日热门）")
                else:
                    r.setdefault("signals", []).append(f"板块冷门减分{boost}（所属板块为今日弱势）")
        # 把板块报告附加到output
        output_sector_report = get_sector_report(sector_perf)
    except Exception as e:
        print(f"[WARN] 板块热度检测失败: {e}")
        output_sector_report = ""

    # ── 大盘趋势调整评分 ──────────────────────────────
    if market_regime == "bearish":
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 熊市模式：所有买入评分下调 -2")
        for r in results:
            if r.get("score", 0) > 0:
                r["score"] = r["score"] - 2
                r.setdefault("signals", []).append("⚠️ 大盘偏空，买入评分下调-2")
    elif market_regime == "bullish":
        # 牛市不额外加分，避免进一步通胀
        pass

    # ── AI 智能分析 ──────────────────────────────
    try:
        from hkstock.analysis.ai_analyzer import run_ai_analysis
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 启动 AI 智能分析...")
        results = run_ai_analysis(results)
        ai_count = len([r for r in results if r.get("ai_analysis")])
        print(f"[{datetime.now().strftime('%H:%M:%S')}] AI 分析完成，{ai_count} 只股票获得 AI 评分")
    except Exception as e:
        print(f"[WARN] AI 分析失败（不影响主流程）: {e}")

    # ── 最终评分钳位 + 重新映射 action / suggested_position_cny ──
    for r in results:
        if r.get("score") == -99:   # 已被基本面排除，保留原标记
            continue
        # 钳位到 [-10, +10]
        r["score"] = clamp_score(r["score"])
        # 重新映射 action
        r["action"] = score_to_action(r["score"])
        # 重新映射建议仓位（结合市场仓位倍数）
        sc = r["score"]
        if sc >= 6:
            base_pos = int(config.TOTAL_CAPITAL * 0.15)
        elif sc >= 4:
            base_pos = int(config.TOTAL_CAPITAL * 0.10)
        elif sc >= 3:
            base_pos = int(config.TOTAL_CAPITAL * 0.05)
        else:
            base_pos = 0
        # 市场仓位倍数调节（bearish 时减仓，bullish 时可适度加仓）
        adjusted_pos = min(int(base_pos * position_multiplier), config.MAX_POSITION)
        # 取 ATR 仓位和调节后仓位的较小值
        atr_pos = r.get("suggested_position_cny", adjusted_pos)
        r["suggested_position_cny"] = min(adjusted_pos, atr_pos) if atr_pos > 0 else adjusted_pos

    # 按评分排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 获取所有股票实时价格（用于看板持仓盈亏）
    try:
        realtime = fetch_realtime(watchlist)
    except Exception:
        realtime = {}

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_real_data": True,
        "stocks": results,
        "realtime": {k: {"price": v["price"], "change_pct": v["change_pct"]} for k, v in realtime.items()},
        "summary": {
            "total_analyzed": len(results),
            "buy_signals": len([r for r in results if "买入" in r["action"]]),
            "sell_signals": len([r for r in results if "卖出" in r["action"]]),
            "hold_signals": len([r for r in results if "观望" in r["action"] or "持有" in r["action"]]),
        },
        "sector_report": output_sector_report,
        "market_regime": market_regime,
        "market_signals": market_signals if market_signals else {},
        "position_multiplier": position_multiplier,
    }

    _data_dir = str(config.DATA_DIR)
    os.makedirs(_data_dir, exist_ok=True)
    with open(os.path.join(_data_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 同步写入数据库
    try:
        from hkstock.data.database import init_db, save_stocks_daily
        init_db()
        save_stocks_daily(results)
    except Exception as e:
        print(f"[WARN] 数据库写入失败（不影响主流程）: {e}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 分析完成，结果已保存到 data/latest.json + 数据库")
    return output

if __name__ == "__main__":
    from hkstock.core import config
    result = run_analysis(config)
    print(f"\n=== 分析摘要 ===")
    print(f"买入信号: {result['summary']['buy_signals']} 只")
    print(f"卖出信号: {result['summary']['sell_signals']} 只")
    print(f"观望: {result['summary']['hold_signals']} 只")
    print(f"\n=== TOP 推荐 ===")
    for s in result["stocks"][:5]:
        print(f"{s['name']}({s['ticker']}) | 价格:{s['price']} | RSI:{s['rsi']} | 评分:{s['score']} | 建议:{s['action']}")
