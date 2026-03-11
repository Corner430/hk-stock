"""
基本面 + 公告数据获取模块
数据来源：
  1. 腾讯行情 API（PE/PB/ROE/股息率/市值等）
  2. 港交所 HKEX 披露易 API（重大公告、业绩预警）
  3. 新浪/东方财富 RSS（财经新闻情绪，第二期）
"""
import requests
import re
import time
import json
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# ─── 第一期：基本面过滤 ────────────────────────────────────

def fetch_fundamentals(tc_code: str) -> dict:
    """
    从腾讯行情 API 拉取基本面数据
    tc_code: 如 hk00700
    返回: {pe, pb, roe, eps, dividend_yield, market_cap_hkd, ps, week52_chg}
    """
    url = f"https://sqt.gtimg.cn/utf8/q=r_{tc_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "utf-8"
        m = re.match(r'v_r_(hk\d+)="([^"]+)"', resp.text.strip())
        if not m:
            return {}
        f = m.group(2).split("~")
        if len(f) < 70:
            return {}

        def safe_float(idx, default=None):
            try:
                v = f[idx].strip()
                return float(v) if v else default
            except (IndexError, ValueError):
                return default

        return {
            "pe":             safe_float(39),    # 市盈率（TTM）
            "pb":             safe_float(47),    # 市净率
            "roe":            safe_float(57),    # 净资产收益率%
            "eps":            safe_float(58),    # 每股收益
            "dividend_yield": safe_float(43),    # 股息率%
            "ps":             safe_float(64),    # 市销率
            "week52_chg":     safe_float(51),    # 52周涨跌%
            "market_cap_hkd": safe_float(69),    # 总市值HKD
        }
    except Exception:
        return {}


def fundamental_filter(ticker: str, tc_code: str, fund: dict, sector: str = None, turnover_hkd: float = 0) -> tuple[bool, list[str]]:
    """
    基本面过滤规则，返回 (通过?, 原因列表)
    """
    reasons = []
    passed = True

    pe = fund.get("pe")
    pb = fund.get("pb")
    roe = fund.get("roe")
    mktcap = fund.get("market_cap_hkd")
    div = fund.get("dividend_yield")

    # 规则1：PE 过高（>200 或负PE且非亏损成长股）
    if pe is not None:
        if pe > 200:
            # AI成长股豁免：高成交额的AI板块股票不按PE过滤
            is_ai = sector and "AI" in sector
            if is_ai and turnover_hkd > 1e8:
                reasons.append(f"PE={pe:.1f} 高但为AI高成长股（豁免）")
            else:
                reasons.append(f"PE={pe:.1f} 过高（>200），估值泡沫风险")
                passed = False
        elif pe < 0:
            # 对亏损的AI股也做豁免说明
            is_ai = sector and "AI" in sector
            if is_ai:
                reasons.append(f"PE={pe:.1f} 为负（AI成长期亏损，可接受）")
            else:
                reasons.append(f"PE={pe:.1f} 为负（亏损），谨慎")
            # 不直接排除，降低评分

    # 规则2：PB < 0（资不抵债）
    if pb is not None and pb < 0:
        reasons.append(f"PB={pb:.2f} 为负（资不抵债），排除")
        passed = False

    # 规则3：市值过小（<5亿HKD，流动性差）
    if mktcap is not None and mktcap > 0 and mktcap < 5e8:
        # 交叉验证：如果该股已通过成交额筛选（5000万+），市值数据可能是API异常
        if mktcap < 1e7:
            reasons.append(f"市值数据异常={mktcap/1e8:.2f}亿HKD（疑似API错误，跳过此规则）")
        else:
            # AI成长股豁免：AI板块 + 成交额>5000万 → 不按市值过滤
            is_ai = sector and "AI" in sector
            if is_ai and turnover_hkd > 5e7:
                reasons.append(f"市值={mktcap/1e8:.1f}亿HKD 偏小但为AI高成交股（豁免）")
            else:
                reasons.append(f"市值={mktcap/1e8:.1f}亿HKD 过小（<5亿），流动性差")
                passed = False

    # 正面加分信息（不过滤，用于报告展示）
    if roe is not None and roe > 15:
        reasons.append(f"ROE={roe:.1f}% 优秀（>15%）")
    if div is not None and div > 3:
        reasons.append(f"股息率={div:.1f}% 高息股")

    return passed, reasons


def fundamental_score_adjust(fund: dict) -> int:
    """
    根据基本面调整评分（-5 到 +5）
    增大基本面权重，让价值股有更大机会被选中
    """
    adj = 0
    pe = fund.get("pe")
    pb = fund.get("pb")
    roe = fund.get("roe")
    div = fund.get("dividend_yield")

    if pe is not None:
        if 5 < pe < 15:    adj += 2   # 低估值（强加分）
        elif 15 <= pe < 25: adj += 1   # 合理偏低
        elif 25 <= pe < 40: adj += 0   # 合理
        elif 40 < pe < 80: adj -= 1   # 偏高
        elif pe > 80:       adj -= 2   # 很高

    if roe is not None:
        if roe > 25:  adj += 2    # 极优秀
        elif roe > 15: adj += 1   # 优秀
        elif roe < 5:  adj -= 1   # 较差

    if pb is not None:
        if 0 < pb < 1.0: adj += 2   # 深度破净
        elif 1.0 <= pb < 1.5: adj += 1   # 低于净资产

    if div is not None:
        if div > 5:   adj += 2  # 超高息
        elif div > 3: adj += 1  # 高息

    return max(-5, min(5, adj))


# ─── 港交所公告（重大事项） ────────────────────────────────

HKEX_SEARCH_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
HKEX_API_URL    = "https://www1.hkexnews.hk/search/titlesearch.xhtml"

# 公告类型：负面关键词
NEGATIVE_KEYWORDS = [
    "盈利警告", "profit warning", "亏损", "loss",
    "调查", "investigation", "清盘", "winding",
    "违规", "违约", "default", "suspension",
    "强制执行", "被告", "诉讼",
]
POSITIVE_KEYWORDS = [
    "回购", "buyback", "增持",
    "业绩增长", "派息", "dividend",
    "股份奖励", "增加股息",
]


def fetch_hkex_announcements(stock_code_5: str, days: int = 3) -> list[dict]:
    """
    从港交所披露易拉取近N天公告
    stock_code_5: 5位港股代码，如 '00700'
    返回公告列表
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    url = (
        f"https://www1.hkexnews.hk/search/titlesearch.xhtml"
        f"?lang=ZH&market=MAINBOARD&category=0&"
        f"stock_code={stock_code_5}&date_from={since}&"
        f"title=&t1code=&t2code=&"
        f"searchType=0&mbType=0"
    )
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www1.hkexnews.hk/"
        }, timeout=10)
        # 港交所返回 HTML，解析标题
        announcements = []
        pattern = re.compile(r'class="ms-vb2"[^>]*>([^<]+)<')
        for m in pattern.finditer(resp.text):
            title = m.group(1).strip()
            if len(title) > 5:
                announcements.append({"title": title})
        return announcements[:10]
    except Exception:
        return []


def analyze_announcements(announcements: list[dict]) -> tuple[int, list[str]]:
    """
    分析公告情绪，返回 (情绪分 -3~+3, 关键公告摘要)
    """
    score = 0
    notes = []
    for ann in announcements:
        title = ann.get("title", "").lower()
        for kw in NEGATIVE_KEYWORDS:
            if kw.lower() in title:
                score -= 2
                notes.append(f"⚠️ 负面公告：{ann['title'][:40]}")
                break
        for kw in POSITIVE_KEYWORDS:
            if kw.lower() in title:
                score += 1
                notes.append(f"✅ 利好公告：{ann['title'][:40]}")
                break
    return max(-3, min(3, score)), notes


# ─── 第二期：财经新闻情绪 ─────────────────────────────────

# ─── 第二期：财经新闻情绪 ─────────────────────────────────

# 大盘情绪缓存（同一次分析只拉一次）
_market_sentiment_cache = None
_market_sentiment_time = 0

# 负面宏观关键词（影响港股整体）
MACRO_NEGATIVE = [
    "暴跌", "崩盘", "恐慌", "抛售", "大跌", "危机", "衰退",
    "加息", "制裁", "战争", "暴雷", "违约", "破产",
    "crash", "plunge", "recession", "sanctions",
]
MACRO_POSITIVE = [
    "大涨", "暴涨", "牛市", "降息", "刺激政策", "量化宽松",
    "利好", "复苏", "创新高", "突破",
    "rally", "surge", "stimulus", "rate cut",
]

# 个股负面关键词
STOCK_NEGATIVE = [
    "盈利警告", "利润警告", "profit warning", "亏损扩大",
    "财务造假", "欺诈", "fraud", "调查", "investigation",
    "清盘", "winding up", "破产", "bankrupt",
    "强制执行", "诉讼", "suspension", "停牌",
    "大幅下调", "评级下调",
]
STOCK_POSITIVE = [
    "业绩超预期", "增持", "回购", "buyback", "大幅盈利",
    "派息增加", "dividend increase", "创历史新高",
    "评级上调", "目标价上调", "重大合同", "战略合作",
]


def fetch_market_sentiment() -> tuple[int, str]:
    """
    拉取宏观财经新闻，判断当前大盘情绪
    返回 (情绪分 -3~+3, 摘要描述)
    """
    global _market_sentiment_cache, _market_sentiment_time
    import time as _time

    # 10分钟缓存
    if _market_sentiment_cache is not None and _time.time() - _market_sentiment_time < 600:
        return _market_sentiment_cache

    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=50&page=1"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
        items = data.get("result", {}).get("data", [])

        neg_count = pos_count = 0
        neg_titles = []
        pos_titles = []

        for item in items:
            title = item.get("title", "").lower()
            summary = item.get("summary", "").lower()
            text = title + summary

            for kw in MACRO_NEGATIVE:
                if kw.lower() in text:
                    neg_count += 1
                    neg_titles.append(item.get("title", "")[:40])
                    break
            for kw in MACRO_POSITIVE:
                if kw.lower() in text:
                    pos_count += 1
                    pos_titles.append(item.get("title", "")[:40])
                    break

        # 情绪评分
        score = 0
        if pos_count > neg_count * 2:
            score = 2
            summary = f"市场情绪偏正面（利好{pos_count}条 vs 利空{neg_count}条）"
        elif pos_count > neg_count:
            score = 1
            summary = f"市场情绪略正面（利好{pos_count}条 vs 利空{neg_count}条）"
        elif neg_count > pos_count * 2:
            score = -2
            summary = f"市场情绪偏负面（利空{neg_count}条 vs 利好{pos_count}条）"
        elif neg_count > pos_count:
            score = -1
            summary = f"市场情绪略负面（利空{neg_count}条 vs 利好{pos_count}条）"
        else:
            score = 0
            summary = f"市场情绪中性（利好{pos_count}条，利空{neg_count}条）"

        if neg_titles:
            summary += f"\n  主要利空：{neg_titles[0]}"
        if pos_titles:
            summary += f"\n  主要利好：{pos_titles[0]}"

        result = (score, summary)
        _market_sentiment_cache = result
        _market_sentiment_time = _time.time()
        return result

    except Exception as e:
        return (0, f"新闻拉取失败: {e}")


def fetch_stock_news_sentiment(stock_name: str, stock_ticker: str) -> tuple[int, list[str]]:
    """
    从腾讯行情 API 拉取个股相关公告摘要（字段 [40]），
    结合全市场新闻关键词匹配，判断个股消息面情绪
    返回 (情绪分 -2~+2, 摘要列表)
    """
    tc_code = "hk" + stock_ticker.replace(".HK", "").zfill(5)
    notes = []
    score = 0

    # 从腾讯行情拉个股附加信息（字段40为公告摘要）
    try:
        url = f"https://sqt.gtimg.cn/utf8/q=r_{tc_code}"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "utf-8"
        m = re.match(r'v_r_(hk\d+)="([^"]+)"', resp.text.strip())
        if m:
            f = m.group(2).split("~")
            announcement = f[40].strip() if len(f) > 40 else ""
            if announcement:
                # 检查是否含负面关键词
                text_lower = announcement.lower()
                for kw in STOCK_NEGATIVE:
                    if kw.lower() in text_lower:
                        score -= 2
                        notes.append(f"⚠️ 负面公告：{announcement[:50]}")
                        break
                for kw in STOCK_POSITIVE:
                    if kw.lower() in text_lower:
                        score += 1
                        notes.append(f"✅ 利好公告：{announcement[:50]}")
                        break
    except Exception:
        pass

    # 从全市场新闻里搜索该股名称
    try:
        url2 = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=50&page=1"
        resp2 = requests.get(url2, headers=HEADERS, timeout=8)
        items = resp2.json().get("result", {}).get("data", [])
        name_short = stock_name[:4]  # 取前4字匹配
        for item in items:
            title = item.get("title", "")
            if name_short in title or stock_ticker.replace(".HK", "") in title:
                text_lower = title.lower()
                for kw in STOCK_NEGATIVE:
                    if kw.lower() in text_lower:
                        score -= 1
                        notes.append(f"⚠️ 相关新闻：{title[:50]}")
                        break
                for kw in STOCK_POSITIVE:
                    if kw.lower() in text_lower:
                        score += 1
                        notes.append(f"📰 相关新闻：{title[:50]}")
                        break
    except Exception:
        pass

    return max(-2, min(2, score)), notes


# ─── 综合基本面分析入口 ───────────────────────────────────

def enrich_with_fundamentals(stock: dict) -> dict:
    """
    对单只股票补充基本面数据，更新 score 和 signals
    stock: analyzer.py 产出的 stock dict（含 ticker, tc_code, score, signals 等）
    """
    tc_code = stock.get("tc_code") or "hk" + stock["ticker"].replace(".HK", "").zfill(5)
    code_5  = tc_code[2:]  # hk00700 → 00700

    # 1. 拉基本面
    fund = fetch_fundamentals(tc_code)
    stock["fundamentals"] = fund

    # 2. 基本面过滤（不通过直接标记）
    # 获取板块信息用于成长股豁免
    try:
        from sector_analyzer import get_sector
        sector = get_sector(stock["ticker"], stock.get("name", ""))
    except Exception:
        sector = None
    turnover_hkd = stock.get("amount_hkd", 0)
    passed, fund_reasons = fundamental_filter(stock["ticker"], tc_code, fund, sector=sector, turnover_hkd=turnover_hkd)
    if not passed:
        stock["score"] = -99
        stock["action"] = "基本面排除"
        stock["signals"] = fund_reasons
        return stock

    # 3. 基本面评分调整
    adj = fundamental_score_adjust(fund)
    stock["score"] = stock.get("score", 0) + adj
    stock["fundamental_notes"] = fund_reasons

    # 4. 港交所公告情绪（只在工作日拉，避免频繁请求）
    announcements = fetch_hkex_announcements(code_5, days=3)
    ann_score, ann_notes = analyze_announcements(announcements)
    stock["score"] += ann_score
    if ann_notes:
        stock["announcement_notes"] = ann_notes
        stock["signals"] = stock.get("signals", []) + ann_notes

    # 5. 新闻情绪（第二期：大盘情绪 + 个股消息面）
    market_score, market_summary = fetch_market_sentiment()
    news_score, news_notes = fetch_stock_news_sentiment(
        stock.get("name", ""), stock["ticker"]
    )
    # 大盘情绪只用一半权重（避免误伤个股）
    total_news_score = round(market_score * 0.5) + news_score
    stock["score"] += total_news_score
    stock["market_sentiment"] = market_summary
    if news_notes:
        stock["signals"] = stock.get("signals", []) + news_notes

    # 把基本面关键指标加进信号说明
    if fund:
        pe_str  = f"PE {fund['pe']:.1f}" if fund.get("pe") else ""
        pb_str  = f"PB {fund['pb']:.2f}" if fund.get("pb") else ""
        roe_str = f"ROE {fund['roe']:.1f}%" if fund.get("roe") else ""
        mktcap  = f"市值{fund['market_cap_hkd']/1e8:.0f}亿" if fund.get("market_cap_hkd") else ""
        summary = " | ".join(filter(None, [pe_str, pb_str, roe_str, mktcap]))
        if summary:
            stock.setdefault("signals", []).append(f"基本面：{summary}")

    return stock


if __name__ == "__main__":
    # 测试：拉几只股票的基本面
    test_stocks = [
        {"ticker": "0700.HK", "tc_code": "hk00700", "name": "腾讯控股",  "score": 2, "signals": []},
        {"ticker": "9988.HK", "tc_code": "hk09988", "name": "阿里巴巴",  "score": 1, "signals": []},
        {"ticker": "1635.HK", "tc_code": "hk01635", "name": "大众公用",  "score": 5, "signals": []},
        {"ticker": "1033.HK", "tc_code": "hk01033", "name": "中石化油服", "score": 4, "signals": []},
    ]
    print("=" * 60)
    print("基本面分析测试")
    print("=" * 60)
    for s in test_stocks:
        result = enrich_with_fundamentals(s)
        fund = result.get("fundamentals", {})
        status = "✅ 通过" if result["score"] != -99 else f"❌ {result['action']}"
        print(f"\n{s['name']} ({s['ticker']})  {status}")
        print(f"  PE={fund.get('pe')}  PB={fund.get('pb')}  ROE={fund.get('roe')}%  "
              f"股息={fund.get('dividend_yield')}%  市值={fund.get('market_cap_hkd',0)/1e8:.0f}亿HKD")
        print(f"  综合评分: {result['score']:+d}")
        for sig in result.get("signals", []):
            print(f"  └ {sig}")
        for note in result.get("fundamental_notes", []):
            print(f"  ★ {note}")
