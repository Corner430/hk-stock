"""
自动模拟交易引擎
- 按买入信号自动开仓（受仓位/资金/板块/相关性约束）
- 触发止损/止盈自动平仓
- 持仓中信号转弱自动减仓/清仓
- 每日记录净值快照
- 盘中持仓检查（独立于完整分析）
"""
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


def sell(portfolio, ticker, name, price, shares, reason, date) -> tuple[bool, str]:
    if ticker not in portfolio["positions"]:
        return False, f"{name} 无持仓"
    pos = portfolio["positions"][ticker]
    if pos["shares"] < shares:
        shares = pos["shares"]

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

    # Step1: 止损/止盈自动平仓
    for alert in check_stop_loss_take_profit(portfolio):
        ticker = alert["ticker"]
        if ticker not in portfolio["positions"]:
            continue
        pos = portfolio["positions"][ticker]
        reason = f"自动{alert['action']}（{alert['pnl_pct']:+.1f}%）"
        ok, msg = sell(portfolio, ticker, pos["name"], alert["current_price"], pos["shares"], reason, today)
        if ok:
            logs.append(msg)

    # Step2: 持仓中信号转弱 → 分级卖出
    stock_map = {s["ticker"]: s for s in stocks}
    for ticker in list(portfolio["positions"].keys()):
        stock_data = stock_map.get(ticker)
        if not stock_data:
            continue
        pos = portfolio["positions"][ticker]
        sc = stock_data.get("score", 0)
        prices = fetch_realtime([ticker])
        price = prices.get(ticker, {}).get("price", pos["avg_cost_hkd"])
        hold_days = calc_hold_days(portfolio, ticker)
        pnl_pct = (price / pos["avg_cost_hkd"] - 1) * 100 if pos["avg_cost_hkd"] > 0 else 0

        if sc <= -5 or "死叉" in " ".join(stock_data.get("signals", [])):
            ok, msg = sell(portfolio, ticker, pos["name"], price, pos["shares"],
                           f"信号严重转弱(评分{sc})，清仓", today)
            if ok:
                logs.append(msg)
        elif sc <= -3 and hold_days >= 3:
            sell_shares = pos["shares"] // 2
            if sell_shares > 0:
                ok, msg = sell(portfolio, ticker, pos["name"], price, sell_shares,
                               f"信号转弱(评分{sc})持{hold_days}天，减仓50%", today)
                if ok:
                    logs.append(msg)
        elif sc <= -1 and hold_days >= 5 and pnl_pct < -3:
            sell_shares = pos["shares"] // 2
            if sell_shares > 0:
                ok, msg = sell(portfolio, ticker, pos["name"], price, sell_shares,
                               f"信号偏弱(评分{sc})持{hold_days}天亏{pnl_pct:.1f}%，减仓", today)
                if ok:
                    logs.append(msg)

    # Step3: 按买入信号自动开仓（评分>=7）
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
        except Exception:
            pass

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

        shares = int((actual_cny / rate) / price / lot_size) * lot_size
        if shares < lot_size:
            shares = lot_size

        ok, msg = buy(portfolio, ticker, name, price, shares,
                      f"评分{s['score']:+d}，{s['signals'][0][:30] if s.get('signals') else ''}", today)
        if ok:
            logs.append(msg)

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
                       alert["current_price"], pos["shares"], reason, today)
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
    except Exception:
        pass
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
