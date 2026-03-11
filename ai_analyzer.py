"""
AI 智能分析模块 - 多模型集成学习（Ensemble）
使用 CodeBuddy Agent SDK 调用多个顶级大语言模型，通过集成学习投票决策
- 集成策略：5个顶级模型独立并行分析，加权投票得出最终结论
- 模型池：Claude-Opus-4.6 / Claude-Sonnet-4.6 / Gemini-3.1-Pro / GPT-5.2 / DeepSeek-V3.1
- 加权方式：旗舰模型高权重，各模型评分取加权平均，多数投票决策
- 并行策略：所有模型并行调用，多只股票同时分析，最大化吞吐
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from codebuddy_agent_sdk import query, CodeBuddyAgentOptions, AssistantMessage, TextBlock, ResultMessage

# ── 配置 ──────────────────────────────────────────────
# API Key（从 .env 或环境变量读取，不在代码中硬编码）
# 请在项目根目录 .env 文件中设置 CODEBUDDY_API_KEY=your_key
import config  # 触发 .env 加载
CODEBUDDY_API_KEY = os.environ.get("CODEBUDDY_API_KEY", "")

# 集成学习模型池（权重越高影响力越大）
# 选用 benchmark 4/4 全通过的顶级模型，旗舰模型权重更高
ENSEMBLE_MODELS = [
    {"model": "claude-opus-4.6",    "weight": 1.5, "name": "Claude-Opus-4.6"},    # 旗舰 x3.40 4/4 6.7s
    {"model": "claude-sonnet-4.6",  "weight": 1.2, "name": "Claude-Sonnet-4.6"},  # 均衡 x2.04 4/4 6.3s
    {"model": "gemini-3.1-pro",     "weight": 1.3, "name": "Gemini-3.1-Pro"},     # Google x1.36 4/4 11.2s
    {"model": "gpt-5.2",            "weight": 1.3, "name": "GPT-5.2"},            # OpenAI x1.33 4/4 (较慢)
    {"model": "deepseek-v3.1",      "weight": 1.0, "name": "DeepSeek-V3.1"},      # DeepSeek 4/4 22.3s
]

AI_TIMEOUT = 180               # 单模型调用超时（秒）GPT-5.2实测需~120s，充裕留余
AI_MAX_CONCURRENT = 10         # 每只股票最大并发模型数（所有模型同时跑）
STOCK_MAX_CONCURRENT = 10      # 同时分析的股票数（全部并行）
AI_TOP_N = 40                  # 对评分前40的股票做AI分析（扩大覆盖）


# ── 核心函数 ──────────────────────────────────────────

async def _call_ai(prompt: str, model: str) -> str:
    """调用单个 AI 模型，返回文本响应"""
    options = CodeBuddyAgentOptions(
        permission_mode="plan",
        model=model,
        max_turns=3,
        allowed_tools=[],
    )
    text = ""
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
    except Exception as e:
        logging.warning(f"[AI] 模型调用失败 ({model}): {e}")
    return text


def _extract_json(text: str) -> dict:
    """从 AI 响应中提取 JSON（兼容 markdown 代码块、多余文字）"""
    # 1. 提取 ```json ... ``` 中的内容
    m = re.search(r'```(?:json)?\s*\n?({[\s\S]*?})\s*\n?```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2. 提取含 "score" 的 JSON 对象
    m = re.search(r'\{[^{}]*"score"[^{}]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 3. 整段解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return {}


def _build_prompt(stock: dict) -> str:
    """构建统一的分析 prompt"""
    name = stock.get("name", stock.get("ticker", "未知"))
    ticker = stock.get("ticker", "")
    price = stock.get("price", 0)
    change = stock.get("change_pct", 0)
    rsi = stock.get("rsi", 50)
    adx = stock.get("adx", 0)
    macd_dir = "多头" if stock.get("macd", 0) > stock.get("macd_signal", 0) else "空头"

    fund = stock.get("fundamentals", {})
    pe = fund.get("pe", "N/A")
    pb = fund.get("pb", "N/A")
    roe = fund.get("roe", "N/A")
    div_yield = fund.get("dividend_yield", "N/A")
    mktcap = fund.get("market_cap_hkd")
    mktcap_str = f"{mktcap/1e8:.0f}亿" if mktcap and mktcap > 0 else "N/A"

    signals = stock.get("signals", [])
    # 过滤掉旧的 AI 信号（避免循环引用）
    signals = [s for s in signals if not s.startswith("🤖") and not s.startswith("⚠️ AI")]
    signals_str = "；".join(signals[:5]) if signals else "无特殊信号"

    sentiment = stock.get("market_sentiment", "未知")

    return f"""你是一位专业港股分析师。请分析以下股票并给出投资评估。

【{name}（{ticker}）】
- 现价: {price} HKD，今日涨跌: {change:+.2f}%
- 技术面: RSI={rsi}, ADX={adx}, MACD方向={macd_dir}
- 基本面: PE={pe}, PB={pb}, ROE={roe}%, 股息率={div_yield}%, 市值={mktcap_str}
- 信号: {signals_str}
- 大盘情绪: {sentiment}

请严格用以下JSON格式回复（不要加任何其他文字）：
{{"score":1到10的整数,"action":"强买/买入/持有/减仓/卖出","reasons":["理由1","理由2","理由3"],"risk":"主要风险"}}

评分标准：1-3=卖出区间, 4-5=观望, 6-7=可以考虑, 8-10=买入机会"""


def _normalize_result(result: dict) -> dict:
    """规范化单个模型的分析结果"""
    if not result or "score" not in result:
        return {}
    try:
        result["score"] = max(1, min(10, int(result["score"])))
    except (ValueError, TypeError):
        result["score"] = 5
    if "action" not in result:
        result["action"] = "持有"
    if "reasons" not in result or not isinstance(result["reasons"], list):
        result["reasons"] = []
    if "risk" not in result:
        result["risk"] = ""
    return result


# ── 集成学习核心 ──────────────────────────────────────

async def _single_model_analyze(prompt: str, model_cfg: dict) -> dict:
    """单个模型对单只股票的分析"""
    model = model_cfg["model"]
    try:
        text = await asyncio.wait_for(
            _call_ai(prompt, model),
            timeout=AI_TIMEOUT
        )
    except asyncio.TimeoutError:
        logging.warning(f"[AI] {model} 超时")
        return {}

    if not text:
        return {}

    result = _extract_json(text)
    result = _normalize_result(result)
    if result:
        result["_model"] = model_cfg["name"]
    return result


async def ensemble_analyze_stock(stock: dict) -> dict:
    """
    集成学习分析：多个模型并行分析同一只股票，加权投票
    返回: {
        "score": 加权平均分,
        "action": 多数投票建议,
        "reasons": 合并后的理由,
        "risk": 合并后的风险,
        "models_used": 参与模型数,
        "model_details": [每个模型的独立结果],
        "consensus": 一致性（0-1, 1=完全一致）
    }
    """
    prompt = _build_prompt(stock)
    ticker = stock.get("ticker", "")

    # 并行调用所有模型，使用 "快速路径" 策略：
    # 当已收到 >=3 个有效结果时，再额外等 30s 后截止，不无限等慢模型
    MIN_MODELS_FOR_VOTE = 3
    FAST_PATH_EXTRA_WAIT = 30  # 快速路径额外等待秒数

    tasks = [asyncio.ensure_future(_single_model_analyze(prompt, cfg)) for cfg in ENSEMBLE_MODELS]

    done_results = []
    pending = set(tasks)
    start = time.time()

    while pending:
        # 等待任意一个完成
        done_batch, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done_batch:
            try:
                res = task.result()
                if res and "score" in res:
                    done_results.append(res)
            except Exception:
                pass

        # 快速路径：已有足够模型返回，设定截止时间
        if len(done_results) >= MIN_MODELS_FOR_VOTE and pending:
            elapsed = time.time() - start
            remaining_timeout = max(1, FAST_PATH_EXTRA_WAIT - (elapsed - 0))
            try:
                extra_done, pending = await asyncio.wait(pending, timeout=remaining_timeout)
                for task in extra_done:
                    try:
                        res = task.result()
                        if res and "score" in res:
                            done_results.append(res)
                    except Exception:
                        pass
            except Exception:
                pass
            # 取消剩余未完成的任务
            for task in pending:
                task.cancel()
            break

    results_raw = done_results

    # 收集有效结果（results_raw 已经过预筛选，全部包含 "score"）
    model_weight_map = {m["name"]: m["weight"] for m in ENSEMBLE_MODELS}
    valid_results = []
    model_details = []
    for res in results_raw:
        model_name = res.get("_model", "Unknown")
        weight = model_weight_map.get(model_name, 1.0)
        valid_results.append((res, weight))
        model_details.append({
            "model": model_name,
            "score": res["score"],
            "action": res["action"],
        })

    if not valid_results:
        return {}

    # ── 加权平均评分 ──
    total_weight = sum(w for _, w in valid_results)
    weighted_score = sum(r["score"] * w for r, w in valid_results) / total_weight
    final_score = round(weighted_score)

    # ── 多数投票决定建议 ──
    # 将 action 归类为 buy/hold/sell 三大类
    def classify_action(action):
        if "买" in action:
            return "买入"
        elif "卖" in action or "减" in action:
            return "卖出"
        return "持有"

    action_votes = Counter()
    for r, w in valid_results:
        category = classify_action(r["action"])
        action_votes[category] += w

    majority_category = action_votes.most_common(1)[0][0]

    # 在多数类别中选择最具体的建议
    specific_actions = [r["action"] for r, _ in valid_results if classify_action(r["action"]) == majority_category]
    # 优先选择更具体的（如"强买"优于"买入"）
    final_action = Counter(specific_actions).most_common(1)[0][0]

    # ── 合并理由（去重） ──
    all_reasons = []
    seen_reasons = set()
    for r, _ in valid_results:
        for reason in r.get("reasons", []):
            # 简单去重：前10个字相同视为重复
            key = reason[:10]
            if key not in seen_reasons:
                seen_reasons.add(key)
                all_reasons.append(reason)
    final_reasons = all_reasons[:4]  # 最多保留4条

    # ── 合并风险提示 ──
    risks = [r.get("risk", "") for r, _ in valid_results if r.get("risk")]
    # 选最长的作为主要风险（通常更详细）
    final_risk = max(risks, key=len) if risks else ""

    # ── 计算一致性（使用标准差，比极差更能反映真实分歧） ──
    scores = [r["score"] for r, _ in valid_results]
    if len(scores) > 1:
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std_dev = variance ** 0.5
        # 标准差 0~4.5 映射到一致性 1~0（std=0 完全一致，std>=4.5 完全不一致）
        consensus = round(max(0, 1 - std_dev / 4.5), 2)
    else:
        consensus = 1.0

    return {
        "score": final_score,
        "action": final_action,
        "reasons": final_reasons,
        "risk": final_risk,
        "models_used": len(valid_results),
        "model_details": model_details,
        "consensus": consensus,
    }


# ── 批量分析 ─────────────────────────────────────────

async def ai_batch_analyze(stocks: list[dict], top_n: int = AI_TOP_N) -> dict:
    """
    批量集成分析：对评分最高的 top_n 只股票进行多模型集成评估
    返回: {ticker: ensemble_result_dict}
    """
    eligible = [
        s for s in stocks
        if s.get("score", 0) != -99
        and s.get("action") not in ("基本面排除", "超买跳过")
    ]
    eligible.sort(key=lambda x: x.get("score", 0), reverse=True)
    to_analyze = eligible[:top_n]

    if not to_analyze:
        return {}

    model_names = "、".join(m["name"] for m in ENSEMBLE_MODELS)
    print(f"[AI集成] 对 {len(to_analyze)} 只股票进行多模型集成分析")
    print(f"[AI集成] 模型池: {model_names}（加权投票）")
    start_time = time.time()

    # 控制同时分析的股票数（每只股票内部已经有多模型并发）
    sem = asyncio.Semaphore(STOCK_MAX_CONCURRENT)

    async def analyze_with_sem(stock):
        async with sem:
            result = await ensemble_analyze_stock(stock)
            if result:
                ticker = stock.get("ticker", "")
                details = result.get("model_details", [])
                scores_str = " / ".join(f"{d['model']}:{d['score']}" for d in details)
                print(f"  ✓ {stock.get('name', ticker):10} 集成分 {result['score']}/10"
                      f"（{scores_str}）一致性 {result['consensus']:.0%}")
            return stock["ticker"], result

    tasks = [analyze_with_sem(s) for s in to_analyze]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    ai_results = {}
    success_count = 0
    for item in results_raw:
        if isinstance(item, Exception):
            logging.warning(f"[AI集成] 分析异常: {item}")
            continue
        ticker, result = item
        if result and "score" in result:
            ai_results[ticker] = result
            success_count += 1

    elapsed = time.time() - start_time
    print(f"[AI集成] 完成: {success_count}/{len(to_analyze)} 成功，耗时 {elapsed:.1f}s")

    return ai_results


def apply_ai_scores(stocks: list[dict], ai_results: dict) -> list[dict]:
    """
    将集成 AI 评分叠加到股票综合评分中
    集成分 1-10 映射为 -3 到 +3 的调整分
    高一致性的结果影响力更大
    """
    for stock in stocks:
        ticker = stock.get("ticker", "")
        if ticker not in ai_results:
            continue
        ai = ai_results[ticker]
        stock["ai_analysis"] = ai

        ai_score = ai.get("score", 5)
        consensus = ai.get("consensus", 0.5)
        models_used = ai.get("models_used", 1)

        # 基础调整分：score 1-10 → -3 to +3
        adjustment = ai_score - 6
        adjustment = max(-3, min(3, adjustment))

        # 一致性加成：如果模型高度一致（>0.8），调整分不打折
        # 如果分歧大（<0.5），调整分减半
        if consensus < 0.5:
            adjustment = round(adjustment * 0.5)
        elif consensus < 0.7:
            adjustment = round(adjustment * 0.75)
        # consensus >= 0.7 保持原值

        # 如果只有1个模型成功，调整分减半（信心不足）
        if models_used <= 1:
            adjustment = round(adjustment * 0.5)

        stock["ai_score_adj"] = adjustment

        if stock.get("score", 0) != -99:
            stock["score"] = stock.get("score", 0) + adjustment

        # 将 AI 集成结论加入信号
        ai_action = ai.get("action", "")
        details = ai.get("model_details", [])
        if ai_action and details:
            model_str = "/".join(f"{d['score']}" for d in details)
            stock.setdefault("signals", []).append(
                f"🤖 AI集成: {ai_action}（{models_used}模型投票 {model_str} → {ai_score}/10, 一致性{consensus:.0%}）"
            )
        for reason in ai.get("reasons", [])[:2]:
            stock.setdefault("signals", []).append(f"🤖 {reason}")
        if ai.get("risk"):
            stock.setdefault("signals", []).append(f"⚠️ AI风险: {ai['risk']}")

    return stocks


def run_ai_analysis(stocks: list[dict]) -> list[dict]:
    """
    同步入口：运行多模型集成 AI 分析并应用评分
    供 analyzer.py 的 run_analysis() 调用
    """
    # 确保 API Key 已设置
    if not os.environ.get("CODEBUDDY_API_KEY"):
        if CODEBUDDY_API_KEY:
            os.environ["CODEBUDDY_API_KEY"] = CODEBUDDY_API_KEY
        else:
            print("[WARN] CODEBUDDY_API_KEY 未设置，请在 .env 文件中配置")
            return stocks
    if not os.environ.get("CODEBUDDY_INTERNET_ENVIRONMENT"):
        os.environ["CODEBUDDY_INTERNET_ENVIRONMENT"] = "internal"

    try:
        ai_results = asyncio.run(ai_batch_analyze(stocks))
        if ai_results:
            stocks = apply_ai_scores(stocks, ai_results)
    except Exception as e:
        logging.warning(f"[AI集成] 分析流程异常（不影响主流程）: {e}")
    return stocks


if __name__ == "__main__":
    # 测试：对一只股票做多模型集成分析
    test_stock = {
        "ticker": "0700.HK",
        "name": "腾讯控股",
        "price": 553.5,
        "change_pct": 7.27,
        "rsi": 57.8,
        "adx": 22.5,
        "macd": 0.5,
        "macd_signal": 0.3,
        "score": 4,
        "signals": ["均线金叉", "MACD多头"],
        "fundamentals": {"pe": 24, "pb": 5.2, "roe": 21, "dividend_yield": 0.6, "market_cap_hkd": 5.2e12},
        "market_sentiment": "市场情绪偏正面",
    }

    async def test():
        print("=" * 60)
        print("多模型集成分析测试")
        print("=" * 60)
        result = await ensemble_analyze_stock(test_stock)
        print(f"\n集成结果：")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(test())
