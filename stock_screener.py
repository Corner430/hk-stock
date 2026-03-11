"""
动态股票筛选模块 v2
覆盖港股主板全量（~2000只），按成交额+波动率动态筛出活跃标的
数据来源：
  1. 恒生指数官方接口 - 获取完整88只成分股（实时更新）
  2. 腾讯批量行情扫描 - 扫港股主板全量代码（00001~04999, 06xxx, 09xxx）
"""
import requests
import re
import time
import json
import logging
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from real_data import fetch_history, HEADERS

HSI_API = "https://www.hsi.com.hk/data/eng/rt/index-series/hsi/constituents.do"

# 港股主板代码范围（腾讯格式）
# 主板: 00001~04999, 新经济/科技: 06000~06999, 09000~09999
SCAN_RANGES = [
    range(1, 5000),      # 主板
    range(6000, 7000),   # 06xxx
    range(9000, 10000),  # 09xxx
]
BATCH_SIZE = 50   # 每次请求50只


def fetch_hsi_constituents() -> list[dict]:
    """从恒生官方接口实时获取HSI成分股（88只）"""
    try:
        r = requests.get(HSI_API, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.hsi.com.hk/"
        }, timeout=10)
        data = r.json()
        stocks = []
        for series in data.get("indexSeriesList", []):
            for idx in series.get("indexList", []):
                if idx.get("indexCode") == "HSI" or "Hang Seng Index" in idx.get("indexName", ""):
                    for s in idx.get("constituentContent", []):
                        code = str(s.get("code", "")).zfill(5)
                        stocks.append({
                            "tc_code": f"hk{code}",
                            "ticker": f"{code.lstrip('0') or '0'}.HK",
                            "name_en": s.get("constituentName", ""),
                        })
        return stocks
    except Exception as e:
        print(f"  [WARN] 恒指官方接口失败: {e}")
        return []


def _fetch_batch(batch_codes: list[int]) -> str:
    """拉取一个批次的行情数据"""
    tc_codes = ",".join([f"r_hk{str(c).zfill(5)}" for c in batch_codes])
    try:
        r = requests.get(f"https://sqt.gtimg.cn/utf8/q={tc_codes}", headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        logging.warning(f"[screener] 批次拉取失败: {e}")
        return ""


def _parse_batch_text(text: str, min_price: float, min_vol: float) -> list[dict]:
    """解析一个批次的响应文本，返回有效股票列表"""
    stocks = []
    for line in text.strip().split("\n"):
        m = re.match(r'v_r_(hk\d+)="([^"]+)"', line)
        if not m:
            continue
        tc = m.group(1)
        f = m.group(2).split("~")
        try:
            name = f[1].strip()
            price = float(f[3]) if f[3] else 0
            prev = float(f[4]) if f[4] else price
            vol = float(f[6]) if f[6] else 0
            if not name or price < min_price or vol < min_vol:
                continue
            code_num = tc[2:]  # hk00700 -> 00700
            ticker = code_num.lstrip("0").zfill(4) + ".HK"
            stocks.append({
                "tc_code": tc,
                "ticker": ticker,
                "name": name,
                "price": price,
                "prev_close": prev,
                "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
                "volume": vol,
                "amount_hkd": price * vol,
            })
        except (IndexError, ValueError):
            continue
    return stocks


def scan_all_hk_stocks(min_price: float = 0.5, min_vol: float = 100) -> list[dict]:
    """
    扫描港股主板全量代码，返回有效股票列表
    min_price: 最低股价（过滤仙股）
    min_vol:   最低成交量（手）
    使用线程池并行拉取，提升扫描速度
    """
    # 将所有代码分批
    batches = []
    for code_range in SCAN_RANGES:
        codes = list(code_range)
        for i in range(0, len(codes), BATCH_SIZE):
            batches.append(codes[i:i + BATCH_SIZE])

    # 并行拉取（最多5个线程）
    results_text = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            text = future.result()
            if text:
                results_text.append(text)

    # 统一解析所有响应
    all_stocks = []
    for text in results_text:
        all_stocks.extend(_parse_batch_text(text, min_price, min_vol))

    return all_stocks


def screen_active_stocks(
    top_n: int = 100,
    min_amount_hkd: float = 5e7,   # 最低成交额5000万HKD
    min_price: float = 1.0,         # 最低股价1HKD
    use_full_scan: bool = True,     # True=全量扫描, False=仅HSI成分股
) -> list[dict]:
    """
    主筛选函数
    1. 来源：全量扫描（~2000只）或仅HSI成分股（88只）
    2. 初筛：成交额 + 股价
    3. 精筛：拉历史数据算波动率，过滤低波动
    4. 排名：活跃度评分 = 成交额70% + 波动率30%
    """
    if use_full_scan:
        print(f"[筛选] 全量扫描港股主板（00001~04999, 06xxx, 09xxx）...")
        raw = scan_all_hk_stocks(min_price=min_price)
        print(f"[筛选] 扫描到有效股票: {len(raw)} 只")
    else:
        print(f"[筛选] 使用HSI成分股（官方实时）...")
        hsi = fetch_hsi_constituents()
        # 补充实时行情
        tc_codes = [s["tc_code"] for s in hsi]
        rt = _fetch_realtime_batch(tc_codes)
        raw = []
        for s in hsi:
            info = rt.get(s["tc_code"])
            if info:
                raw.append({**s, **info})

    # 初筛：成交额 + 股价
    candidates = [s for s in raw if s.get("amount_hkd", 0) >= min_amount_hkd and s.get("price", 0) >= min_price]
    amt_display = f"{min_amount_hkd/1e8:.1f}亿" if min_amount_hkd >= 1e8 else f"{min_amount_hkd/1e4:.0f}万"
    print(f"[筛选] 成交额≥{amt_display}HKD + 股价≥{min_price}HKD: {len(candidates)} 只")

    # 精筛：拉历史数据计算波动率 + 质量过滤
    qualified = []
    for c in candidates:
        df = fetch_history(c["ticker"], days=60)
        # ★ 质量过滤1：历史数据不足15天 → 太新，跳过
        if df is None or len(df) < 15:
            continue
        # ★ 质量过滤2：近5日成交额均值 vs 长期均值，暴涨超过10倍 → 游资炒作，跳过
        if "Volume" in df.columns and "Close" in df.columns:
            amt_series = df["Close"] * df["Volume"]
            lookback = min(20, len(amt_series))
            if lookback >= 10:
                avg_long = float(amt_series.tail(lookback).mean())
                avg_5 = float(amt_series.tail(5).mean())
                if avg_long > 0 and avg_5 / avg_long > 10:
                    continue   # 近期爆炒，不碰
        vol = float(df["ChangePercent"].tail(20).abs().mean()) if "ChangePercent" in df.columns else 0
        c["volatility"] = round(vol, 2)
        # 活跃度评分
        amt_score = min(c["amount_hkd"] / 5e10, 1.0)   # 500亿HKD封顶
        vol_score = min(vol / 5.0, 1.0)                 # 5%波动率封顶
        c["activity_score"] = round(amt_score * 0.7 + vol_score * 0.3, 4)
        if vol >= 0.8:
            qualified.append(c)

    qualified.sort(key=lambda x: x["activity_score"], reverse=True)
    selected = qualified[:top_n]
    print(f"[筛选] 最终筛出 {len(selected)} 只活跃标的（来自 {len(qualified)} 只候选）")
    return selected


def _fetch_realtime_batch(tc_codes: list) -> dict:
    results = {}
    for i in range(0, len(tc_codes), 20):
        batch = tc_codes[i:i+20]
        codes = ",".join([f"r_{c}" for c in batch])
        try:
            resp = requests.get(f"https://sqt.gtimg.cn/utf8/q={codes}", headers=HEADERS, timeout=10)
            resp.encoding = "utf-8"
            for line in resp.text.strip().split("\n"):
                m = re.match(r'v_r_(hk\d+)="([^"]+)"', line)
                if not m:
                    continue
                tc = m.group(1)
                f = m.group(2).split("~")
                try:
                    price = float(f[3]) if f[3] else 0
                    prev = float(f[4]) if f[4] else price
                    vol = float(f[6]) if f[6] else 0
                    if price > 0:
                        results[tc] = {
                            "name": f[1],
                            "price": price,
                            "prev_close": prev,
                            "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
                            "volume": vol,
                            "amount_hkd": price * vol,
                        }
                except (IndexError, ValueError):
                    continue
        except Exception as e:
            logging.warning(f"[screener] 实时行情批量拉取失败: {e}")
        time.sleep(0.1)
    return results


def get_dynamic_watchlist(top_n: int = 100, use_full_scan: bool = True) -> tuple[list, dict]:
    """
    对外接口：返回 (ticker_list, name_cache)
    """
    stocks = screen_active_stocks(top_n=top_n, use_full_scan=use_full_scan)
    tickers = [s["ticker"] for s in stocks]
    names = {s["ticker"]: s.get("name", s.get("name_en", s["ticker"])) for s in stocks}
    return tickers, names


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    print("=" * 60)
    if mode == "hsi":
        print("  模式：仅HSI成分股（88只官方实时）")
        stocks = screen_active_stocks(top_n=50, use_full_scan=False)
    else:
        print("  模式：港股主板全量扫描")
        stocks = screen_active_stocks(top_n=50, use_full_scan=True)
    print("=" * 60)

    print(f"\n{'排名':<4} {'股票名':14} {'代码':10} {'价格':>8} {'涨跌':>7} {'成交额(亿HKD)':>13} {'波动率':>7}")
    print("-" * 65)
    for i, s in enumerate(stocks, 1):
        name = s.get("name") or s.get("name_en", "?")
        print(f"{i:<4} {name:14} {s['ticker']:10} "
              f"{s['price']:>8.2f} {s.get('change_pct', 0):>+6.1f}% "
              f"{s['amount_hkd']/1e8:>12.1f}  "
              f"{s.get('volatility', 0):>6.1f}%")

    print(f"\n✅ 共筛出 {len(stocks)} 只，覆盖面远超原来的15只预设")
