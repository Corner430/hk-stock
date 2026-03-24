"""
真实港股数据抓取 - 使用腾讯股票接口（无需API Key，完全免费）
实时行情 + 历史K线，均可用
"""
from __future__ import annotations

import requests
import pandas as pd
import json
import re
import logging
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}

def ticker_to_tencent(ticker: str) -> str:
    """0700.HK → hk00700, HSI.HI → hkHSI"""
    if ticker.upper() in ("HSI.HI", "HSI"):
        return "hkHSI"
    code = ticker.replace(".HK", "").replace(".hk", "").zfill(5)
    return f"hk{code}"

def fetch_realtime(tickers: list[str]) -> dict[str, dict]:
    """
    批量获取实时行情
    返回 {ticker: {name, price, change, change_pct, volume, ...}}
    """
    codes = ",".join([f"r_{ticker_to_tencent(t)}" for t in tickers])
    url = f"https://sqt.gtimg.cn/utf8/q={codes}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        result = {}
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            m = re.match(r'v_r_(hk\d+)="([^"]+)"', line)
            if not m:
                continue
            raw_code = m.group(1)   # hk00700
            fields = m.group(2).split("~")
            # 字段说明：0=未知 1=名称 2=代码 3=当前价 4=昨收 5=今开 6=成交量 ...
            ticker_key = raw_code[2:].lstrip("0") or "0"
            ticker_key = ticker_key.zfill(4) + ".HK"
            # 修正5位代码
            for t in tickers:
                if ticker_to_tencent(t) == raw_code:
                    ticker_key = t
                    break
            try:
                price = float(fields[3]) if fields[3] else 0
                prev_close = float(fields[4]) if fields[4] else price
                change = price - prev_close
                change_pct = round(change / prev_close * 100, 2) if prev_close else 0
                lot_size = int(float(fields[60])) if len(fields) > 60 and fields[60].strip() else 100
                result[ticker_key] = {
                    "name": fields[1],
                    "price": price,
                    "prev_close": prev_close,
                    "change": round(change, 3),
                    "change_pct": change_pct,
                    "volume": int(float(fields[6])) if fields[6] else 0,
                    "lot_size": lot_size,
                    "updated_at": fields[30] if len(fields) > 30 else "",
                }
            except (IndexError, ValueError):
                continue
        return result
    except Exception as e:
        logging.error(f"[real_data] 实时行情获取失败: {e}")
        return {}

def fetch_history(ticker: str, days: int = 90) -> pd.DataFrame | None:
    """
    获取历史日K线数据，返回 DataFrame
    列：Date(index), Open, Close, High, Low, Volume, ChangePercent
    """
    code = ticker_to_tencent(ticker)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?_var=kline_day&param={code},day,{start_date},{end_date},320,qfq"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        raw = resp.text

        m = re.search(r'=(\{.*\})', raw)
        if not m:
            return None

        d = json.loads(m.group(1))
        # 路径：data -> hkXXXXX -> day
        stock_data = d.get("data", {}).get(code, {})
        rows = stock_data.get("day", stock_data.get("qfqday", []))

        if not rows:
            return None

        records = []
        for row in rows:
            try:
                records.append({
                    "Date": pd.to_datetime(row[0]),
                    "Open": float(row[1]),
                    "Close": float(row[2]),
                    "High": float(row[3]),
                    "Low": float(row[4]),
                    "Volume": float(row[5]),
                })
            except (IndexError, ValueError):
                continue

        df = pd.DataFrame(records).set_index("Date").sort_index()

        # 计算涨跌幅
        df["ChangePercent"] = df["Close"].pct_change() * 100
        df["ChangePercent"] = df["ChangePercent"].round(2)

        # 只保留最近 days 天
        return df.tail(days)

    except Exception as e:
        logging.error(f"[real_data] {ticker} 历史数据获取失败: {e}")
        return None


if __name__ == "__main__":
    print("=== 测试实时行情 ===")
    rt = fetch_realtime(["0700.HK", "9988.HK", "1211.HK", "0005.HK", "0388.HK"])
    for ticker, info in rt.items():
        print(f"  {info['name']}({ticker}) 现价:{info['price']} 涨跌:{info['change_pct']:+.2f}%")

    print("\n=== 测试历史K线（腾讯） ===")
    df = fetch_history("0700.HK", days=10)
    if df is not None:
        print(df[["Open", "Close", "Volume", "ChangePercent"]].tail(5))
    else:
        print("获取失败")
