"""
自动模拟交易引擎
- 按买入信号自动开仓（受仓位/资金/板块/相关性约束）
- 触发止损/止盈自动平仓
- 持仓中信号转弱自动减仓/清仓
- 每日记录净值快照
- 盘中持仓检查（独立于完整分析）
"""
import logging
from datetime import datetime
import config
from position_manager import (
    load_portfolio, save_portfolio,
    check_position_limits, check_stop_loss_take_profit,
    calc_hold_days, calc_trade_fee_hkd, get_hkd_to_cny,
)
from real_data import fetch_realtime


# ── 交易执行 ──────────────────────────────────────────────

def buy(portfolio, ticker, name, price, shares, reason, date) -> tuple[bool, str]:
    cost_hkd = price * shares
    fee_hkd = calc_trade_fee_hkd(cost_hkd)
    rate = get_hkd_to_cny()
    cost_cny = cost_hkd * rate
    fee_cny = fee_hkd * rate
    total_cost = cost_cny + fee_cny

    if total_cost > portfolio["cash_cny"]:
        return False, f"{name} 现金不足"

    portfolio["cash_cny"] -= total_cost

    if ticker not in portfolio["positions"]:
        portfolio["positions"][ticker] = {
            "name": name, "shares": 0,
            "avg_cost_hkd": 0, "total_cost_cny": 0,
        }
    pos = portfolio["positions"][ticker]
    old_shares = pos["shares"]
    old_cost = pos["avg_cost_hkd"] * old_shares
    new_shares = old_shares + shares
    pos["avg_cost_hkd"] = round((old_cost + cost_hkd) / new_shares, 3)
    pos["shares"] = new_shares
    pos["total_cost_cny"] = round(pos["total_cost_cny"] + total_cost, 2)

    portfolio["trades"].append({
        "date": date, "action": "BUY", "ticker": ticker, "name": name,
        "shares": shares, "price_hkd": price,
        "cost_cny": round(total_cost, 2), "reason": reason,
    })
    return True, f"买入 {name}（{ticker}）{shares}股 @{price}HKD  花费¥{total_cost:.0f}"


def sell(portfolio, ticker, name, price, shares, reason, date,
         lot_size: int = None) -> tuple[bool, str]:
    if ticker not in portfolio["positions"]:
        return False, f"{name} 无持仓"
    pos = portfolio["positions"][ticker]
    if pos["shares"] < shares:
        shares = pos["shares"]

    # 碎股处理：如果卖出后剩余不足1手，则全部卖出
    if lot_size and lot_size > 0:
        remaining = pos["shares"] - shares
        if 0 < remaining < lot_size:
            shares = pos["shares"]  # 避免产生碎股

    revenue_hkd = price * shares
    fee_hkd = calc_trade_fee_hkd(revenue_hkd)
    rate = get_hkd_to_cny()
    revenue_cny = revenue_hkd * rate
    fee_cny = fee_hkd * rate
    net_revenue = revenue_cny - fee_cny

    avg_cost = pos["avg_cost_hkd"]
    pnl_cny = (price - avg_cost) * shares * rate
    pnl_pct = round((price / avg_cost - 1) * 100, 2) if avg_cost > 0 else 0

    portfolio["cash_cny"] += net_revenue
    pos["shares"] -= shares
    if pos["shares"] == 0:
        del portfolio["positions"][ticker]

    portfolio["trades"].append({
        "date": date, "action": "SELL", "ticker": ticker, "name": name,
        "shares": shares, "price_hkd": price,
        "revenue_cny": round(net_revenue, 2),
        "pnl_cny": round(pnl_cny, 2), "pnl_pct": pnl_pct, "reason": reason,
    })
    return True, f"卖出 {name}（{ticker}）{shares}股 @{price}HKD  {pnl_pct:+.1f}%（¥{pnl_cny:+.0f}）"


# ── 每日自动交易 ──────────────────────────────────────────

def auto_trade(analysis_data: dict) -> list[str]:
    """根据分析结果自动执行模拟交易，返回操作日志"""
    portfolio = load_portfolio()
    stocks = analysis_data.get("stocks", [])
    today = datetime.now().strftime("%Y-%m-%d")
    logs = []

    # Step0: 组合回撤保护
    drawdown_halt = _check_drawdown(portfolio, logs)

    # Step1: 止损/止盈自动平仓（含时间止损 + 分批止盈）
    for alert in check_stop_loss_take_profit(portfolio):
        ticker = alert["ticker"]
        if ticker not in portfolio["positions"]:
            continue
        pos = portfolio["positions"][ticker]
        reason = f"自动{alert['action']}（{alert['pnl_pct']:+.1f}%）"

        # 分批止盈：只卖出 partial_shares，并记录已执行的级别
        if "partial_shares" in alert:
            sell_shares = alert["partial_shares"]
            ok, msg = sell(portfolio, ticker, pos["name"], alert["current_price"],
                           sell_shares, reason, today, lot_size=pos.get("lot_size", 100))
            if ok:
                logs.append(msg)
                # 记录已执行的止盈级别，避免重复触发
                if ticker in portfolio["positions"]:
                    tp_executed = portfolio["positions"][ticker].setdefault("tp_executed", [])
                    tp_executed.append(alert["tp_level_index"])
        else:
            # 全仓止损/止盈/时间止损
            ok, msg = sell(portfolio, ticker, pos["name"], alert["current_price"],
                           pos["shares"], reason, today, lot_size=pos.get("lot_size", 100))
            if ok:
                logs.append(msg)

    # Step2: 持仓中信号转弱 → 分级卖出
    stock_map = {s["ticker"]: s for s in stocks}
    # 批量获取持仓实时价格（避免 N+1 HTTP 调用）
    _held_tickers = [t for t in portfolio["positions"] if t in stock_map]
    _held_prices = fetch_realtime(_held_tickers) if _held_tickers else {}
    for ticker in list(portfolio["positions"].keys()):
        stock_data = stock_map.get(ticker)
        if not stock_data:
            continue
        pos = portfolio["positions"][ticker]
        sc = stock_data.get("score", 0)
        price = _held_prices.get(ticker, {}).get("price", pos["avg_cost_hkd"])
        hold_days = calc_hold_days(portfolio, ticker)
        pnl_pct = (price / pos["avg_cost_hkd"] - 1) * 100 if pos["avg_cost_hkd"] > 0 else 0

        if sc <= -5 or "死叉" in " ".join(stock_data.get("signals", [])):
            ok, msg = sell(portfolio, ticker, pos["name"], price, pos["shares"],
                           f"信号严重转弱(评分{sc})，清仓", today, lot_size=pos.get("lot_size", 100))
            if ok:
                logs.append(msg)
        elif sc <= -3 and hold_days >= 3:
            sell_shares = pos["shares"] // 2
            if sell_shares > 0:
                ok, msg = sell(portfolio, ticker, pos["name"], price, sell_shares,
                               f"信号转弱(评分{sc})持{hold_days}天，减仓50%", today, lot_size=pos.get("lot_size", 100))
                if ok:
                    logs.append(msg)
        elif sc <= -1 and hold_days >= 5 and pnl_pct < -3:
            sell_shares = pos["shares"] // 2
            if sell_shares > 0:
                ok, msg = sell(portfolio, ticker, pos["name"], price, sell_shares,
                               f"信号偏弱(评分{sc})持{hold_days}天亏{pnl_pct:.1f}%，减仓", today, lot_size=pos.get("lot_size", 100))
                if ok:
                    logs.append(msg)

    # Step3a: 分批建仓续建
    stock_map_3a = {s["ticker"]: s for s in stocks}
    for ticker in list(portfolio["positions"].keys()):
        pos = portfolio["positions"].get(ticker)
        if not pos or "pending_build" not in pos:
            continue
        pb = pos["pending_build"]
        if not pb.get("ratios") or pb.get("remaining_shares", 0) < pb.get("lot_size", 100):
            del pos["pending_build"]
            continue
        sd = stock_map_3a.get(ticker)
        if sd and sd.get("score", 0) < 0:
            logs.append(f"取消续建 {pos['name']}：评分{sd['score']}转负")
            del pos["pending_build"]
            continue
        rt = fetch_realtime([ticker])
        cur_price = rt.get(ticker, {}).get("price", 0)
        if cur_price <= 0:
            continue
        lot_size_pb = pb["lot_size"]
        next_ratio = pb["ratios"][0]
        batch_shares = max(lot_size_pb, int(pb["total_target_shares"] * next_ratio / lot_size_pb) * lot_size_pb)
        batch_shares = min(batch_shares, pb["remaining_shares"])
        batch_shares = (batch_shares // lot_size_pb) * lot_size_pb
        if batch_shares < lot_size_pb:
            del pos["pending_build"]
            continue
        rate = get_hkd_to_cny()
        batch_cost_cny = cur_price * batch_shares * rate
        available = portfolio["cash_cny"] - config.RESERVE_CASH
        if batch_cost_cny > available:
            logs.append(f"续建 {pos['name']} 资金不足，暂缓")
            continue
        built_count = pb["built_count"] + 1
        total_batches = built_count + len(pb["ratios"]) - 1
        ok, msg = buy(portfolio, ticker, pos["name"], cur_price, batch_shares,
                      f"分批建仓续建({built_count}/{total_batches})", today)
        if ok:
            logs.append(msg)
            pb["ratios"] = pb["ratios"][1:]
            pb["remaining_shares"] -= batch_shares
            pb["built_count"] = built_count
            if ticker in portfolio["positions"]:
                portfolio["positions"][ticker]["lot_size"] = lot_size_pb
            if not pb["ratios"] or pb["remaining_shares"] < lot_size_pb:
                if ticker in portfolio["positions"] and "pending_build" in portfolio["positions"][ticker]:
                    del portfolio["positions"][ticker]["pending_build"]

    # Step3: 按买入信号自动开仓（评分>=7）（回撤停买时跳过）
    buy_signals = []
    if drawdown_halt:
        logs.append("☢️ 组合回撤超限，停止新开仓")
    else:
        buy_signals = [
            s for s in stocks
            if s.get("score", 0) >= 7
            and s.get("action") not in ["基本面排除", "超买跳过"]
            and s["ticker"] not in portfolio["positions"]
        ]

    for s in buy_signals:
        can_buy, reason = check_position_limits(portfolio)
        if not can_buy:
            logs.append(f"跳过 {s.get('name', s['ticker'])}：{reason}")
            break

        # 板块集中度检查
        try:
            from sector_analyzer import get_sector
            sector = get_sector(s["ticker"], s.get("name", ""))
            sector_count = sum(
                1 for t in portfolio["positions"]
                if get_sector(t) == sector and sector != "其他"
            )
            if sector_count >= 2:
                logs.append(f"跳过 {s.get('name', s['ticker'])}：{sector}板块已持{sector_count}只")
                continue
        except Exception as e:
            logging.warning("板块集中度检查失败: %s", e)

        ticker = s["ticker"]
        name = s.get("name", ticker)
        price = s.get("price", 0)
        position_cny = min(s.get("suggested_position_cny", 0), config.MAX_POSITION)

        if price <= 0 or position_cny <= 0:
            continue

        # 持仓相关性检查
        if _check_correlation(ticker, portfolio):
            logs.append(f"跳过 {name}：与现有持仓相关性过高")
            continue

        # 资金约束
        invested = sum(p.get("total_cost_cny", 0) for p in portfolio["positions"].values())
        remaining_quota = config.MAX_INVESTED_CNY - invested
        available_cash = portfolio["cash_cny"] - config.RESERVE_CASH
        actual_cny = min(position_cny, remaining_quota, available_cash)
        if actual_cny < 2000:
            logs.append(f"跳过 {name}：资金不足")
            continue

        # 获取真实每手股数并计算股数
        rt_info = fetch_realtime([ticker])
        lot_size = rt_info.get(ticker, {}).get("lot_size", 100)
        rate = get_hkd_to_cny()
        min_lot_cost_cny = price * lot_size * rate

        # 一手都超过仓位上限或可用资金，跳过
        if min_lot_cost_cny > actual_cny:
            logs.append(f"跳过 {name}：一手需¥{min_lot_cost_cny:,.0f}，超过可用额度¥{actual_cny:,.0f}")
            continue

        total_shares = int((actual_cny / rate) / price / lot_size) * lot_size
        if total_shares < lot_size:
            total_shares = lot_size

        # 分批建仓：第一批即时买入，剩余批次记录待建仓计划
        first_ratio = config.PARTIAL_BUILD_RATIOS[0]
        first_shares = max(lot_size, int(total_shares * first_ratio / lot_size) * lot_size)

        ok, msg = buy(portfolio, ticker, name, price, first_shares,
                      f"评分{s['score']:+d}，分批建仓(1/{len(config.PARTIAL_BUILD_RATIOS)})  "
                      f"{s['signals'][0][:25] if s.get('signals') else ''}", today)
        if ok:
            logs.append(msg)
            # 存储lot_size供卖出时碎股保护
            if ticker in portfolio["positions"]:
                portfolio["positions"][ticker]["lot_size"] = lot_size
            # 记录待建仓计划
            remaining_shares = total_shares - first_shares
            if remaining_shares >= lot_size and ticker in portfolio["positions"]:
                portfolio["positions"][ticker]["pending_build"] = {
                    "total_target_shares": total_shares,
                    "remaining_shares": remaining_shares,
                    "lot_size": lot_size,
                    "ratios": config.PARTIAL_BUILD_RATIOS[1:],
                    "built_count": 1,
                }

    # Step4: 记录净值快照
    _snapshot(portfolio, today)
    save_portfolio(portfolio)
    return logs


# ── 盘中持仓检查 ─────────────────────────────────────────

def run_intraday_check() -> list[str]:
    """盘中仅检查止损/止盈，不做完整分析，不新买入"""
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    if not positions:
        return ["空仓，无需检查"]

    today = datetime.now().strftime("%Y-%m-%d")
    logs = []
    alerts = check_stop_loss_take_profit(portfolio)

    if not alerts:
        try:
            prices = fetch_realtime(list(positions.keys()))
            for ticker, pos in positions.items():
                if ticker in prices:
                    cur = prices[ticker]["price"]
                    avg = pos.get("avg_cost_hkd", 0)
                    pnl = (cur / avg - 1) * 100 if avg > 0 else 0
                    logs.append(f"  {pos.get('name', ticker)}: {cur:.3f} HKD ({pnl:+.1f}%)")
        except Exception:
            pass
        logs.insert(0, f"持仓检查 - {len(positions)} 只，无止损/止盈触发")
        return logs

    for alert in alerts:
        ticker = alert["ticker"]
        if ticker not in portfolio["positions"]:
            continue
        pos = portfolio["positions"][ticker]
        reason = f"盘中自动{alert['action']}（{alert['pnl_pct']:+.1f}%）"
        ok, msg = sell(portfolio, ticker, pos.get("name", ticker),
                       alert["current_price"], pos["shares"], reason, today,
                       lot_size=pos.get("lot_size", 100))
        if ok:
            logs.append(msg)

    save_portfolio(portfolio)
    logs.insert(0, f"盘中检查 - 触发 {len(alerts)} 个警报")
    return logs


# ── 内部工具 ─────────────────────────────────────────────

def _check_correlation(ticker: str, portfolio: dict) -> bool:
    """检查新股票与现有持仓的价格相关性，>0.85 返回 True"""
    if not portfolio["positions"]:
        return False
    try:
        from real_data import fetch_history
        new_df = fetch_history(ticker, days=30)
        if new_df is None or len(new_df) < 20:
            return False
        new_returns = new_df["Close"].pct_change().dropna()
        for pos_ticker in portfolio["positions"]:
            pos_df = fetch_history(pos_ticker, days=30)
            if pos_df is None or len(pos_df) < 20:
                continue
            pos_returns = pos_df["Close"].pct_change().dropna()
            common = new_returns.index.intersection(pos_returns.index)
            if len(common) >= 15:
                corr = new_returns.loc[common].corr(pos_returns.loc[common])
                if corr > 0.85:
                    return True
    except Exception as e:
        logging.warning("相关性检查失败: %s", e)
    return False


def _check_drawdown(portfolio: dict, logs: list) -> bool:
    """
    组合级别回撤保护。
    - 回撤 > DRAWDOWN_WARN_PCT: 警告
    - 回撤 > DRAWDOWN_HALT_PCT: 停止新开仓
    - 回撤 > DRAWDOWN_REDUCE_PCT: 强制减仓50%
    返回 True 表示应停止新开仓。
    """
    snaps = portfolio.get("daily_snapshots", [])
    if len(snaps) < 2:
        return False

    # 找历史最高净值
    peak = max(s.get("total_value_cny", 0) for s in snaps)
    current = snaps[-1].get("total_value_cny", portfolio["total_capital_cny"])
    if peak <= 0:
        return False

    drawdown = (peak - current) / peak

    if drawdown >= config.DRAWDOWN_REDUCE_PCT:
        logs.append(f"🚨 组合回撤 {drawdown:.1%} 超过减仓线 {config.DRAWDOWN_REDUCE_PCT:.0%}，强制减仓50%")
        today = datetime.now().strftime("%Y-%m-%d")
        for ticker in list(portfolio["positions"].keys()):
            pos = portfolio["positions"].get(ticker)
            if not pos:
                continue
            lot_size_dd = pos.get("lot_size", 100)
            sell_shares = (pos["shares"] // 2 // lot_size_dd) * lot_size_dd
            if sell_shares <= 0:
                sell_shares = pos["shares"]  # 不足1手就全卖
            if sell_shares > 0:
                prices = fetch_realtime([ticker])
                price = prices.get(ticker, {}).get("price", pos["avg_cost_hkd"])
                ok, msg = sell(portfolio, ticker, pos["name"], price, sell_shares,
                               f"回撤{drawdown:.1%}强制减仓50%", today, lot_size=lot_size_dd)
                if ok:
                    logs.append(msg)
        return True
    elif drawdown >= config.DRAWDOWN_HALT_PCT:
        logs.append(f"⚠️ 组合回撤 {drawdown:.1%} 超过停买线 {config.DRAWDOWN_HALT_PCT:.0%}，停止新开仓")
        return True
    elif drawdown >= config.DRAWDOWN_WARN_PCT:
        logs.append(f"⚠️ 组合回撤 {drawdown:.1%} 超过警告线 {config.DRAWDOWN_WARN_PCT:.0%}")

    return False


def _snapshot(portfolio: dict, today: str):
    """记录每日净值"""
    positions = portfolio.get("positions", {})
    pos_value = 0.0
    if positions:
        try:
            prices = fetch_realtime(list(positions.keys()))
            rate = get_hkd_to_cny()
            for tk, pos in positions.items():
                if tk in prices:
                    pos_value += prices[tk]["price"] * pos["shares"] * rate
        except Exception:
            pass

    total = portfolio["cash_cny"] + pos_value
    capital = portfolio["total_capital_cny"]
    snap = {
        "date": today,
        "total_value_cny": round(total, 2),
        "cash_cny": round(portfolio["cash_cny"], 2),
        "position_value_cny": round(pos_value, 2),
        "return_pct": round((total / capital - 1) * 100, 2),
        "total_return_cny": round(total - capital, 2),
        "n_positions": len(positions),
    }
    snaps = portfolio.setdefault("daily_snapshots", [])
    portfolio["daily_snapshots"] = [s for s in snaps if s["date"] != today] + [snap]


def get_trade_summary(portfolio: dict) -> str:
    """交易统计摘要"""
    trades = portfolio.get("trades", [])
    sells = [t for t in trades if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl_cny", 0) > 0]

    snaps = portfolio.get("daily_snapshots", [])
    latest = snaps[-1] if snaps else {}
    total_val = latest.get("total_value_cny", portfolio["total_capital_cny"])
    ret_pct = latest.get("return_pct", 0.0)
    start = portfolio.get("created_at", "未知")

    lines = [
        f"模拟账户（{start} 至今）",
        f"  起始资金：¥{portfolio['total_capital_cny']:,}",
        f"  当前净值：¥{total_val:,.0f}  {ret_pct:+.2f}%",
        f"  累计交易：{len(trades)}次（买入{len(trades) - len(sells)}次，卖出{len(sells)}次）",
    ]
    if sells:
        total_pnl = sum(t.get("pnl_cny", 0) for t in sells)
        win_rate = round(len(wins) / len(sells) * 100)
        lines.append(f"  胜率：{win_rate}%  已实现盈亏：¥{total_pnl:+.0f}")
    return "\n".join(lines)


if __name__ == "__main__":
    p = load_portfolio()
    print(get_trade_summary(p))
