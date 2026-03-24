"""
仓位管理 + 持仓跟踪 + 止损止盈检测 + 交易费用计算 + 汇率
"""
import json
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows: file locking not available
import math
import os
import time
import logging
from datetime import datetime
import requests
from hkstock.core import config
from hkstock.data.real_data import fetch_realtime

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "portfolio.json")

# ── 汇率 ──────────────────────────────────────────────────

_rate_cache = {"value": None, "time": 0}


def get_hkd_to_cny() -> float:
    """获取 HKD/CNY 汇率，4 小时缓存"""
    now = time.time()
    if _rate_cache["value"] and now - _rate_cache["time"] < 14400:
        return _rate_cache["value"]
    try:
        resp = requests.get("https://open.er-api.com/v6/latest/HKD", timeout=8)
        rate = round(resp.json()["rates"]["CNY"], 6)
        _rate_cache["value"] = rate
        _rate_cache["time"] = now
        return rate
    except Exception as e:
        logging.warning(f"[position] 获取 HKD/CNY 汇率失败: {e}")
        return _rate_cache["value"] or 0.885


# ── 交易费用 ──────────────────────────────────────────────

def calc_trade_fee_hkd(amount_hkd: float) -> float:
    """
    港股单边交易费用（港元）
    券商佣金 0.03% (min 3)  +  平台费 15/笔  +  SFC 0.00278%
    HKEX 0.00565%  +  CCASS 0.002% (min 2)  +  印花税 0.1% (ceil to 1) [2024年起]
    """
    return round(
        max(3.0, amount_hkd * 0.0003)
        + 15.0
        + amount_hkd * 0.0000278
        + amount_hkd * 0.0000565
        + max(2.0, amount_hkd * 0.00002)
        + max(1.0, math.ceil(amount_hkd * 0.001)),
        2,
    )


# ── 组合持久化 ────────────────────────────────────────────

def load_portfolio() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)
    return {
        "total_capital_cny": config.TOTAL_CAPITAL,
        "cash_cny": config.TOTAL_CAPITAL,
        "positions": {},
        "trades": [],
        "daily_snapshots": [],
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }


def save_portfolio(portfolio: dict):
    import tempfile
    dir_path = os.path.dirname(PORTFOLIO_FILE)
    os.makedirs(dir_path, exist_ok=True)
    # 原子写入：先写临时文件再 rename，避免文件截断竞态
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PORTFOLIO_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── 仓位限制检查 ──────────────────────────────────────────

def check_position_limits(portfolio) -> tuple[bool, str]:
    positions = portfolio.get("positions", {})
    n = len(positions)

    if n >= config.MAX_POSITIONS:
        return False, f"已持 {n} 只，达上限 {config.MAX_POSITIONS}"

    invested = sum(p.get("total_cost_cny", 0) for p in positions.values())
    if invested >= config.MAX_INVESTED_CNY:
        return False, f"已投入 ¥{invested:,.0f}，达上限 ¥{config.MAX_INVESTED_CNY:,}"

    available = portfolio.get("cash_cny", 0) - config.RESERVE_CASH
    if available < 2000:
        return False, f"现金 ¥{portfolio.get('cash_cny', 0):,.0f} 扣除保留金后不足"

    remaining = config.MAX_INVESTED_CNY - invested
    return True, f"可买入（已投 ¥{invested:,.0f}，余额 ¥{remaining:,.0f}，持仓 {n}/{config.MAX_POSITIONS}）"


# ── 止损止盈检测 ──────────────────────────────────────────

def check_stop_loss_take_profit(portfolio) -> list[dict]:
    positions = portfolio.get("positions", {})
    if not positions:
        return []

    try:
        prices = fetch_realtime(list(positions.keys()))
    except Exception as e:
        logging.warning(f"[position] 获取实时行情失败: {e}")
        return []

    rate = get_hkd_to_cny()
    alerts = []

    for ticker, pos in positions.items():
        if ticker not in prices:
            continue
        current_price = prices[ticker]["price"]
        avg_cost = pos.get("avg_cost_hkd", 0)
        if avg_cost <= 0:
            continue

        pnl_pct = (current_price / avg_cost - 1) * 100
        shares = pos.get("shares", 0)
        pnl_cny = (current_price - avg_cost) * shares * rate
        hold_days = calc_hold_days(portfolio, ticker)

        # 更新最高价水位线
        high_wm = pos.get("high_watermark_hkd", avg_cost)
        if current_price > high_wm:
            high_wm = current_price
            pos["high_watermark_hkd"] = high_wm

        # 动态止损线
        if pnl_pct > 10:
            trailing_stop = high_wm * 0.95
        elif pnl_pct > 5:
            trailing_stop = avg_cost * 1.02
        else:
            trailing_stop = avg_cost * (1 - config.STOP_LOSS_PCT)

        if current_price <= trailing_stop:
            action = "跟踪止盈" if pnl_pct > 0 else "止损"
            alerts.append({
                "ticker": ticker,
                "name": pos.get("name", ticker),
                "action": action,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_cny": round(pnl_cny, 2),
                "current_price": current_price,
                "avg_cost": avg_cost,
                "hold_days": hold_days,
            })
        elif pnl_pct >= config.TAKE_PROFIT_PCT * 100:
            alerts.append({
                "ticker": ticker,
                "name": pos.get("name", ticker),
                "action": "止盈",
                "pnl_pct": round(pnl_pct, 2),
                "pnl_cny": round(pnl_cny, 2),
                "current_price": current_price,
                "avg_cost": avg_cost,
                "hold_days": hold_days,
            })
        # 时间止损：持仓超过 TIME_STOP_DAYS 天且涨幅不足
        elif hold_days >= config.TIME_STOP_DAYS and pnl_pct < config.TIME_STOP_MIN_GAIN_PCT:
            alerts.append({
                "ticker": ticker,
                "name": pos.get("name", ticker),
                "action": "时间止损",
                "pnl_pct": round(pnl_pct, 2),
                "pnl_cny": round(pnl_cny, 2),
                "current_price": current_price,
                "avg_cost": avg_cost,
                "hold_days": hold_days,
            })
        else:
            # 分批止盈检查：按 PARTIAL_TP_LEVELS 分批卖出
            tp_executed = pos.get("tp_executed", [])  # 已执行的止盈级别索引
            for i, (tp_pct, tp_sell_ratio) in enumerate(config.PARTIAL_TP_LEVELS):
                if i in tp_executed:
                    continue
                if pnl_pct >= tp_pct * 100:
                    sell_shares = int(shares * tp_sell_ratio)
                    if sell_shares > 0:
                        alerts.append({
                            "ticker": ticker,
                            "name": pos.get("name", ticker),
                            "action": f"分批止盈({i+1}/{len(config.PARTIAL_TP_LEVELS)})",
                            "pnl_pct": round(pnl_pct, 2),
                            "pnl_cny": round(pnl_cny, 2),
                            "current_price": current_price,
                            "avg_cost": avg_cost,
                            "hold_days": hold_days,
                            "partial_shares": sell_shares,
                            "tp_level_index": i,
                        })
                    break  # 每次只触发一个级别

    save_portfolio(portfolio)
    return alerts


# ── 持仓摘要 ──────────────────────────────────────────────

def get_positions_summary(portfolio) -> str:
    positions = portfolio.get("positions", {})
    if not positions:
        return "当前空仓"

    try:
        prices = fetch_realtime(list(positions.keys()))
    except Exception:
        prices = {}

    rate = get_hkd_to_cny()
    lines = [f"当前持仓（{len(positions)}/{config.MAX_POSITIONS}）"]
    total_pnl = 0.0

    for ticker, pos in positions.items():
        name = pos.get("name", ticker)
        avg_cost = pos.get("avg_cost_hkd", 0)
        shares = pos.get("shares", 0)
        hold_days = calc_hold_days(portfolio, ticker)

        if ticker in prices:
            cur = prices[ticker]["price"]
            pnl_pct = (cur / avg_cost - 1) * 100 if avg_cost else 0
            pnl_cny = (cur - avg_cost) * shares * rate
            total_pnl += pnl_cny
            lines.append(
                f"  {name}（{ticker}）持 {hold_days}天  "
                f"{avg_cost:.3f}->{cur:.3f} HKD  {pnl_pct:+.1f}%  ¥{pnl_cny:+.0f}"
            )
        else:
            lines.append(f"  {name}（{ticker}）成本 {avg_cost:.3f} HKD")

    cash = portfolio.get("cash_cny", 0)
    lines.append(f"\n  现金：¥{cash:,.0f}  持仓盈亏：¥{total_pnl:+.0f}")
    return "\n".join(lines)


# ── 工具函数 ──────────────────────────────────────────────

def calc_hold_days(portfolio, ticker) -> int:
    for t in reversed(portfolio.get("trades", [])):
        if t.get("ticker") == ticker and t.get("action") == "BUY":
            try:
                buy_date = datetime.strptime(t["date"][:10], "%Y-%m-%d")
                return (datetime.now() - buy_date).days
            except Exception:
                return 0
    return 0


if __name__ == "__main__":
    p = load_portfolio()
    print(get_positions_summary(p))
    print()
    can_buy, reason = check_position_limits(p)
    print(f"可买入: {can_buy}  {reason}")
    alerts = check_stop_loss_take_profit(p)
    if alerts:
        for a in alerts:
            print(f"  {a['action']}: {a['name']} {a['pnl_pct']:+.1f}%")
    else:
        print("无止损/止盈触发")
