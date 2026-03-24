"""
P1-A：IPO / 新股追踪模块
自动检测近期港股新上市股票，纳入观察池
数据来源：腾讯行情 API 扫描 + 历史数据天数判断
"""
import requests
import re
import json
import os
import time
import logging
from datetime import datetime, timedelta
from hkstock.data.real_data import fetch_realtime, HEADERS

IPO_WATCH_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ipo_watchlist.json")
IPO_MAX_AGE_DAYS = 90   # 追踪上市后 90 天内的新股


def load_ipo_watchlist() -> list[dict]:
    if os.path.exists(IPO_WATCH_FILE):
        with open(IPO_WATCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_ipo_watchlist(watchlist: list[dict]):
    os.makedirs(os.path.dirname(IPO_WATCH_FILE), exist_ok=True)
    with open(IPO_WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)


def detect_new_listings(scan_days: int = 120) -> list[dict]:
    """
    扫描港股主板，通过以下方式识别新股：
    1. 拉历史K线，如果数据点 < scan_days，说明是近期上市
    2. 只考虑 30~89 天的（太新的技术指标不可用，太老的不算新股）
    返回新股列表
    """
    from hkstock.data.real_data import fetch_history

    # 全主板扫描（覆盖 0001-2999, 6000-9999）
    # 0100.HK(MiniMax)、2513.HK(智谱) 等都能覆盖到
    candidates = []
    scan_codes = (
        list(range(1, 500)) +       # 0001-0499（老编号新上市如0100 MiniMax）
        list(range(500, 2000)) +     # 0500-1999
        list(range(2000, 3000)) +    # 2000-2999（常见新股区间）
        list(range(6000, 7000)) +    # 06xxx 新经济
        list(range(9000, 10000))     # 09xxx 双重上市
    )

    print(f"  [IPO扫描] 扫描 {len(scan_codes)} 个代码段...")

    # 批量拉实时行情，先过滤掉不活跃的
    BATCH = 50
    active_codes = []
    for i in range(0, len(scan_codes), BATCH):
        batch = scan_codes[i:i+BATCH]
        tc_codes = ",".join([f"r_hk{str(c).zfill(5)}" for c in batch])
        try:
            r = requests.get(
                f"https://sqt.gtimg.cn/utf8/q={tc_codes}",
                headers=HEADERS, timeout=10
            )
            r.encoding = "utf-8"
            for line in r.text.strip().split("\n"):
                m = re.match(r'v_r_(hk\d+)="([^"]+)"', line)
                if not m:
                    continue
                f = m.group(2).split("~")
                if len(f) < 40:
                    continue
                price = float(f[3]) if f[3] else 0
                vol   = float(f[37]) if f[37] else 0  # 成交额
                name  = f[1]
                code  = m.group(1)[2:]  # hk02xxx → 02xxx
                if price >= 0.5 and vol >= 1e7 and name:  # 有价格 + 有成交 + 有名字
                    active_codes.append({
                        "tc_code": m.group(1),
                        "ticker":  f"{int(code)}.HK",
                        "name":    name,
                        "price":   price,
                        "vol_hkd": vol,
                    })
        except Exception as e:
            logging.warning(f"[ipo] 批量拉取新股行情失败: {e}")
        time.sleep(0.1)

    print(f"  [IPO扫描] 活跃候选: {len(active_codes)} 只，检查历史数据天数...")

    new_listings = []
    for stock in active_codes:
        try:
            df = fetch_history(stock["ticker"], days=scan_days)
            if df is None or len(df) == 0:
                continue
            days_of_data = len(df)
            # 30~89天数据 = 近期新股，技术指标已可计算
            if 20 <= days_of_data < scan_days - 5:
                # 估算上市日期
                first_date = df.index[0].strftime("%Y-%m-%d") if hasattr(df.index[0], "strftime") else str(df.index[0])
                stock["days_listed"] = days_of_data
                stock["first_trade_date"] = first_date
                # 计算上市以来涨跌幅
                first_price = float(df["Close"].iloc[0])
                stock["ipo_chg_pct"] = round((stock["price"] / first_price - 1) * 100, 2)
                new_listings.append(stock)
                time.sleep(0.05)
        except Exception as e:
            logging.warning(f"[ipo] 检查新股历史数据失败 {stock.get('ticker', '?')}: {e}")

    new_listings.sort(key=lambda x: x["days_listed"])
    return new_listings


def update_ipo_watchlist() -> list[dict]:
    """
    更新新股观察池：每天运行一次
    - 扫描新上市的股票加入
    - 超过 90 天的移出
    """
    existing = load_ipo_watchlist()
    existing_tickers = {s["ticker"] for s in existing}

    # 移除超龄的
    today = datetime.now()
    kept = []
    for s in existing:
        try:
            listed = datetime.strptime(s["first_trade_date"], "%Y-%m-%d")
            age = (today - listed).days
            if age <= IPO_MAX_AGE_DAYS:
                s["days_listed"] = age
                kept.append(s)
        except Exception as e:
            logging.warning(f"[ipo] 解析新股上市日期失败: {e}")
            kept.append(s)

    # 扫描新增
    new_found = detect_new_listings()
    added = []
    for s in new_found:
        if s["ticker"] not in existing_tickers:
            s["added_at"] = today.strftime("%Y-%m-%d")
            added.append(s)

    result = kept + added
    save_ipo_watchlist(result)
    return result


def get_ipo_report(watchlist: list[dict]) -> str:
    """生成新股观察报告"""
    if not watchlist:
        return ""

    # 按上市涨幅排序
    watchlist.sort(key=lambda x: x.get("ipo_chg_pct", 0), reverse=True)

    lines = [f"\n🆕 新股观察（近90天上市，共{len(watchlist)}只）"]
    for s in watchlist[:8]:
        days  = s.get("days_listed", "?")
        chg   = s.get("ipo_chg_pct", 0)
        chg_s = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"  • {s['name']:12} {s['ticker']:10} "
            f"上市{days}天 {chg_s}  价格{s['price']:.2f}HKD"
        )

    return "\n".join(lines)


def get_ipo_tickers_for_analysis(min_days: int = 15) -> tuple[list[str], dict[str, str]]:
    """
    返回已有足够交易天数的 IPO 股票，直接注入分析管道。
    绕过 stock_screener 的历史数据门槛，确保新股不被遗漏。
    返回: (ticker_list, name_cache)
    """
    watchlist = load_ipo_watchlist()
    tickers = []
    names = {}
    for s in watchlist:
        days = s.get("days_listed", 0)
        if days >= min_days:
            ticker = s["ticker"]
            tickers.append(ticker)
            names[ticker] = s.get("name", ticker)
    return tickers, names


if __name__ == "__main__":
    print("扫描新股中（需要几分钟）...")
    wl = update_ipo_watchlist()
    print(f"\n找到 {len(wl)} 只新股：")
    for s in wl:
        print(f"  {s['name']:12} {s['ticker']:10} 上市{s['days_listed']}天 "
              f"IPO涨幅{s.get('ipo_chg_pct',0):+.1f}%")
