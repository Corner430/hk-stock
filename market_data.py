"""
港股市场数据模块 - 获取南向资金、AH溢价、VHSI、卖空比率等市场级信号
所有函数在网络异常时返回 {"valid": False}，不影响主流程
"""
import time
import logging
import requests

logger = logging.getLogger(__name__)

# ── 全局缓存 ──
_cache: dict = {}
_CACHE_TTL = 600  # 10 分钟


def _get_cached(key: str):
    """获取缓存数据，过期返回 None"""
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data: dict):
    """写入缓存"""
    _cache[key] = {"data": data, "ts": time.time()}


# ────────────────────────────────────────────
# 1. 南向资金（沪深港通南向）
# ────────────────────────────────────────────
def fetch_southbound_flow() -> dict:
    """
    获取南向资金净流入数据（港股通）。
    数据源：东方财富 API
    返回: {"valid": True, "net_buy": float(亿港元), "signal": "bullish"|"bearish"|"neutral"}
    """
    cached = _get_cached("southbound")
    if cached:
        return cached

    try:
        # 东方财富港股通资金流向 API
        url = "https://push2.eastmoney.com/api/qt/kamtbs.rtmin/get"
        params = {
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "cb": "",
            "rt": int(time.time()),
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # 解析南向净买入（沪港通南+深港通南，单位：万元 → 亿港元）
        # data.data.s2n 格式: 南向净买入
        s2n = data.get("data", {})
        # NOTE: f2/f4 单位为万元（人民币），转换为亿元需除以10000
        net_buy_hgt = float(s2n.get("f2", 0))  # 沪→港 净买入（万元）
        net_buy_sgt = float(s2n.get("f4", 0))  # 深→港 净买入（万元）
        net_buy_total = (net_buy_hgt + net_buy_sgt) / 10000  # 万→亿

        if net_buy_total > 20:
            signal = "bullish"
        elif net_buy_total < -10:
            signal = "bearish"
        else:
            signal = "neutral"

        result = {"valid": True, "net_buy": round(net_buy_total, 2), "signal": signal}
        _set_cache("southbound", result)
        return result

    except Exception as e:
        logger.warning("获取南向资金数据失败: %s", e)
        return {"valid": False, "net_buy": 0, "signal": "neutral"}


# ────────────────────────────────────────────
# 2. AH 溢价指数
# ────────────────────────────────────────────
def fetch_ah_premium() -> dict:
    """
    获取恒生AH溢价指数 (HSAHP)。
    高溢价 → A贵H便宜 → 对港股有利。
    返回: {"valid": True, "index": float, "signal": "bullish"|"bearish"|"neutral"}
    """
    cached = _get_cached("ah_premium")
    if cached:
        return cached

    try:
        # 东方财富 AH 溢价指数
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "100.HSAHP",
            "fields": "f43,f44,f45,f46,f47,f48,f170",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # NOTE: f43 返回值已验证为实际值*100，需除以100还原。如数值异常请检查API变更
        index_val = float(data.get("data", {}).get("f43", 0)) / 100

        # AH溢价 > 130 → A股明显偏贵，南向资金更有动力买H股
        if index_val > 135:
            signal = "bullish"
        elif index_val < 110:
            signal = "bearish"
        else:
            signal = "neutral"

        result = {"valid": True, "index": round(index_val, 2), "signal": signal}
        _set_cache("ah_premium", result)
        return result

    except Exception as e:
        logger.warning("获取AH溢价指数失败: %s", e)
        return {"valid": False, "index": 0, "signal": "neutral"}


# ────────────────────────────────────────────
# 3. VHSI 恒指波动率指数
# ────────────────────────────────────────────
def fetch_vhsi() -> dict:
    """
    获取 VHSI（恒指波动率指数），类似VIX。
    高 VHSI → 市场恐慌 → 应减小仓位。
    返回: {"valid": True, "vhsi": float, "signal": "low_vol"|"high_vol"|"extreme_vol"}
    """
    cached = _get_cached("vhsi")
    if cached:
        return cached

    try:
        # 东方财富获取 VHSI
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "100.VHSI",
            "fields": "f43,f170",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # NOTE: f43 返回值已验证为实际值*100，需除以100还原。如数值异常请检查API变更
        vhsi_val = float(data.get("data", {}).get("f43", 0)) / 100

        if vhsi_val > 30:
            signal = "extreme_vol"
        elif vhsi_val > 20:
            signal = "high_vol"
        else:
            signal = "low_vol"

        result = {"valid": True, "vhsi": round(vhsi_val, 2), "signal": signal}
        _set_cache("vhsi", result)
        return result

    except Exception as e:
        logger.warning("获取VHSI失败: %s", e)
        return {"valid": False, "vhsi": 0, "signal": "low_vol"}


# ────────────────────────────────────────────
# 4. 市场活跃度（HSI换手率）
# ────────────────────────────────────────────
def fetch_market_activity() -> dict:
    """
    获取恒生指数换手率，作为市场交易活跃度的衡量指标。
    高换手率 → 市场交易活跃（可能是情绪波动或资金进出频繁）。
    低换手率 → 市场交投清淡。
    注意：此指标为HSI换手率，并非卖空比率。
    返回: {"valid": True, "turnover_rate": float(%), "signal": "high"|"normal"|"low"}
    """
    cached = _get_cached("market_activity")
    if cached:
        return cached

    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "100.HSI",
            "fields": "f43,f44,f45,f46,f47,f48,f49,f50,f170",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # HSI 换手率（f50），反映市场整体交易活跃程度
        turnover_rate = float(data.get("data", {}).get("f50", 0)) / 100

        if turnover_rate > 18:
            signal = "high"    # 交投非常活跃
        elif turnover_rate < 8:
            signal = "low"     # 交投清淡
        else:
            signal = "normal"

        result = {"valid": True, "turnover_rate": round(turnover_rate, 2), "signal": signal}
        _set_cache("market_activity", result)
        return result

    except Exception as e:
        logger.warning("获取市场活跃度数据失败: %s", e)
        return {"valid": False, "turnover_rate": 0, "signal": "normal"}


# ────────────────────────────────────────────
# 5. 美股隔夜表现
# ────────────────────────────────────────────
def fetch_us_overnight() -> dict:
    """
    获取美股主要指数隔夜表现，影响港股开盘。
    返回: {"valid": True, "sp500_pct": float, "nasdaq_pct": float, "signal": "bullish"|"bearish"|"neutral"}
    """
    cached = _get_cached("us_overnight")
    if cached:
        return cached

    try:
        results = {}
        # 标普500
        for name, secid in [("sp500", "100.SPX"), ("nasdaq", "100.NDX")]:
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": secid,
                "fields": "f43,f170",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            pct = float(data.get("data", {}).get("f170", 0)) / 100
            results[name] = round(pct, 2)

        avg_pct = (results.get("sp500", 0) + results.get("nasdaq", 0)) / 2
        if avg_pct > 1.0:
            signal = "bullish"
        elif avg_pct < -1.0:
            signal = "bearish"
        else:
            signal = "neutral"

        result = {
            "valid": True,
            "sp500_pct": results.get("sp500", 0),
            "nasdaq_pct": results.get("nasdaq", 0),
            "signal": signal,
        }
        _set_cache("us_overnight", result)
        return result

    except Exception as e:
        logger.warning("获取美股隔夜数据失败: %s", e)
        return {"valid": False, "sp500_pct": 0, "nasdaq_pct": 0, "signal": "neutral"}


# ────────────────────────────────────────────
# 6. MSCI 调仓日历
# ────────────────────────────────────────────
def check_msci_rebalance() -> dict:
    """
    检查当前是否接近 MSCI 调仓日（季度调仓：2月/5月/8月/11月最后一个交易日前后）。
    调仓期间被动资金大幅流动，影响股价。
    返回: {"valid": True, "near_rebalance": bool, "days_to_rebalance": int}
    """
    import datetime

    cached = _get_cached("msci_rebalance")
    if cached:
        return cached

    try:
        today = datetime.date.today()
        # MSCI 季度调仓月：2, 5, 8, 11 的最后一个交易日（近似为最后一个工作日）
        rebalance_months = [2, 5, 8, 11]

        min_days = 999
        for month in rebalance_months:
            year = today.year
            # 下一个调仓月
            if month < today.month:
                year += 1
            elif month == today.month:
                # 当月，检查是否还在调仓窗口
                pass

            # 该月最后一天
            if month == 12:
                last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
            else:
                last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

            # 调整到最后一个工作日
            while last_day.weekday() >= 5:  # 5=周六, 6=周日
                last_day -= datetime.timedelta(days=1)

            days_diff = (last_day - today).days
            if days_diff < 0:
                days_diff += 365  # 今年已过，算明年

            if abs(days_diff) < abs(min_days):
                min_days = days_diff

        near_rebalance = abs(min_days) <= 5  # 5个交易日内视为调仓窗口

        result = {
            "valid": True,
            "near_rebalance": near_rebalance,
            "days_to_rebalance": min_days,
        }
        _set_cache("msci_rebalance", result)
        return result

    except Exception as e:
        logger.warning("MSCI调仓检查失败: %s", e)
        return {"valid": False, "near_rebalance": False, "days_to_rebalance": 999}


# ────────────────────────────────────────────
# 7. 综合市场信号
# ────────────────────────────────────────────
def get_market_signals() -> dict:
    """
    汇总所有市场级信号，返回综合评估。
    用于 analyzer.py 的 market_regime 判断和仓位调节。

    返回:
        {
            "valid": True,
            "southbound": {...},
            "ah_premium": {...},
            "vhsi": {...},
            "market_activity": {...},
            "us_overnight": {...},
            "msci": {...},
            "overall_sentiment": "bullish"|"bearish"|"neutral",
            "position_multiplier": 0.3 ~ 1.2,
        }
    """
    sb = fetch_southbound_flow()
    ah = fetch_ah_premium()
    vhsi = fetch_vhsi()
    sr = fetch_market_activity()
    us = fetch_us_overnight()
    msci = check_msci_rebalance()

    # ── 综合打分 ──
    score = 0

    # 南向资金权重最高（±3）
    if sb.get("signal") == "bullish":
        score += 3
    elif sb.get("signal") == "bearish":
        score -= 3

    # AH溢价（±2）
    if ah.get("signal") == "bullish":
        score += 2
    elif ah.get("signal") == "bearish":
        score -= 2

    # VHSI 波动率（±2）
    if vhsi.get("signal") == "extreme_vol":
        score -= 2
    elif vhsi.get("signal") == "high_vol":
        score -= 1

    # 市场活跃度（±1）— 基于HSI换手率
    # 高活跃度可能意味着市场波动加大，略微谨慎；低活跃度交投清淡，观望
    if sr.get("signal") == "high":
        score -= 1   # 异常活跃可能伴随波动风险
    elif sr.get("signal") == "low":
        score += 1   # 低活跃度时筛选出的信号更可靠

    # 美股隔夜（±2）
    if us.get("signal") == "bullish":
        score += 2
    elif us.get("signal") == "bearish":
        score -= 2

    # MSCI 调仓期间降低仓位
    if msci.get("near_rebalance"):
        score -= 1

    # 综合情绪
    if score >= 4:
        sentiment = "bullish"
    elif score <= -3:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    # 仓位倍数：bullish 可以满仓甚至适度加杠杆(1.2)，bearish 只用30%仓位
    if score >= 6:
        position_multiplier = 1.2
    elif score >= 3:
        position_multiplier = 1.0
    elif score >= 0:
        position_multiplier = 0.8
    elif score >= -3:
        position_multiplier = 0.6
    else:
        position_multiplier = 0.3

    return {
        "valid": True,
        "southbound": sb,
        "ah_premium": ah,
        "vhsi": vhsi,
        "market_activity": sr,
        "us_overnight": us,
        "msci": msci,
        "score": score,
        "overall_sentiment": sentiment,
        "position_multiplier": position_multiplier,
    }
