"""
每日港股分析报告生成 v3
整合：技术面 + 基本面 + 消息面 + 仓位管理 + 板块热度 + 新股追踪
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import config
import analyzer

WECOM_TARGET = config.WECOM_TARGET
SERVER_IP    = config.SERVER_IP


def generate_report(data: dict, portfolio=None) -> str:
    stocks  = data["stocks"]
    summary = data["summary"]
    now     = data["generated_at"]

    # ── 分类 ──────────────────────────────────────────────
    buy_stocks   = [s for s in stocks if s.get("score", 0) >= 7
                    and s.get("action") not in ["基本面排除"]]
    watch_stocks = [s for s in stocks if 3 <= s.get("score", 0) < 7
                    and s.get("action") not in ["基本面排除"]]
    fund_excl    = [s for s in stocks if s.get("action") == "基本面排除"]
    overbought   = [s for s in stocks if s.get("score", 0) <= -4
                    and s.get("action") not in ["基本面排除"]]

    # ── 仓位状态 ─────────────────────────────────────────
    from position_manager import (
        load_portfolio, check_position_limits,
        check_stop_loss_take_profit, get_positions_summary
    )
    if portfolio is None:
        portfolio = load_portfolio()
    can_buy, buy_limit_reason = check_position_limits(portfolio)
    alerts = check_stop_loss_take_profit(portfolio)
    positions_text = get_positions_summary(portfolio)

    # ── 板块热度 ─────────────────────────────────────────
    sector_report = ""
    hot_sectors   = []
    try:
        from sector_analyzer import fetch_sector_performance, get_sector_report, get_hot_sectors
        sector_perf = fetch_sector_performance()
        sector_report = get_sector_report(sector_perf)
        hot_sectors   = get_hot_sectors(sector_perf)
    except Exception as e:
        sector_report = f"  （板块数据获取失败：{e}）"

    # ── 新股追踪 ─────────────────────────────────────────
    ipo_report = ""
    try:
        from ipo_tracker import load_ipo_watchlist, get_ipo_report
        ipo_wl     = load_ipo_watchlist()
        ipo_report = get_ipo_report(ipo_wl)
    except Exception:
        pass

    # ── 大盘情绪 ─────────────────────────────────────────
    mkt_emoji = "⚠️"
    mkt_line  = ""
    try:
        from fundamentals import fetch_market_sentiment
        mkt_score, mkt_summary = fetch_market_sentiment()
        if mkt_score >= 1:
            mkt_emoji = "✅"
        elif mkt_score <= -2:
            mkt_emoji = "🔴"
        mkt_line = f"🌐 大盘情绪：{mkt_emoji} {mkt_summary.split(chr(10))[0]}"
        if hot_sectors:
            mkt_line += f"\n🔥 强势板块：{'、'.join(hot_sectors)}"
    except Exception:
        mkt_line = "🌐 大盘情绪：数据获取中"

    # ── 拼接报告 ─────────────────────────────────────────
    lines = []
    lines.append(f"📊 港股每日分析报告 | {now[:10]}")
    lines.append(f"全量扫描 → 精选 {summary['total_analyzed']} 只活跃标的")
    lines.append("━" * 28)

    # 大盘情绪
    lines.append(mkt_line)

    # 止损/止盈警报（最优先）
    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(a["msg"])

    # 仓位限制提示
    if not can_buy:
        lines.append(f"\n🚫 {buy_limit_reason}")

    lines.append("")
    lines.append("━" * 28)

    # 买入信号
    if buy_stocks and can_buy:
        lines.append(f"🟢 买入信号（{len(buy_stocks)}只）— 三重确认")
        for i, s in enumerate(buy_stocks[:5], 1):
            fund = s.get("fundamentals", {})
            pe   = f"PE {fund['pe']:.0f}" if fund.get("pe") else ""
            roe  = f"ROE {fund['roe']:.0f}%" if fund.get("roe") else ""
            div  = f"息 {fund['dividend_yield']:.0f}%" if fund.get("dividend_yield") else ""
            basics = " | ".join(filter(None, [pe, roe, div]))
            chg_s  = f"+{s['change_pct']}%" if s.get("change_pct", 0) >= 0 else f"{s['change_pct']}%"
            # 板块加成标注
            sector = ""
            try:
                from sector_analyzer import get_sector
                sec = get_sector(s["ticker"])
                if sec in hot_sectors:
                    sector = f" 🔥{sec}"
            except Exception:
                pass
            sig = s["signals"][0] if s.get("signals") else "技术指标综合"
            lines.append(f"\n{i}. {s['name']}（{s['ticker']}）评分{s['score']:+d}{sector}")
            lines.append(f"   {s['price']} HKD {chg_s} | RSI {s['rsi']:.1f}")
            lines.append(f"   基本面：{basics}")
            lines.append(f"   💡 建议仓位 ¥{s['suggested_position_cny']:,}")
            lines.append(f"   ▷ {sig[:60]}")
            # AI 分析
            ai = s.get("ai_analysis")
            if ai:
                models = ai.get("models_used", 1)
                consensus = ai.get("consensus", 0)
                details = ai.get("model_details", [])
                detail_str = " / ".join(f"{d['model']}:{d['score']}" for d in details)
                lines.append(f"   🤖 AI集成: {ai.get('action','?')}（{ai.get('score','?')}/10, {models}模型, 一致性{consensus:.0%}）")
                if detail_str:
                    lines.append(f"   📊 {detail_str}")
                if ai.get("risk"):
                    lines.append(f"   ⚠️ {ai['risk'][:50]}")
            # 负面公告提醒
            for note in s.get("announcement_notes", []):
                lines.append(f"   {note}")
    elif buy_stocks and not can_buy:
        lines.append(f"🟢 买入信号 {len(buy_stocks)} 只（仓位已满，仅供观察）")
        for s in buy_stocks[:3]:
            lines.append(f"  • {s['name']} {s['ticker']} 评分{s['score']:+d}")
    else:
        lines.append("今日暂无买入信号，市场观望为主")

    # 关注名单
    if watch_stocks:
        lines.append(f"\n🟡 关注名单（{len(watch_stocks)}只，等更强信号）")
        for s in watch_stocks[:5]:
            sig = s["signals"][0][:30] if s.get("signals") else ""
            lines.append(f"  • {s['name']:10} {s['ticker']:10} RSI {s['rsi']:.1f}  {sig}")

    # 基本面排除
    if fund_excl:
        excl_names = "、".join([s.get("name", s["ticker"]) for s in fund_excl[:5]])
        lines.append(f"\n❌ 基本面排除（{len(fund_excl)}只）：{excl_names}")

    # 超买跳过
    if overbought:
        ob_names = "、".join([s.get("name", s["ticker"]) for s in overbought[:5]])
        lines.append(f"⛔ 超买跳过（{len(overbought)}只）：{ob_names}")

    # 持仓状态
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(positions_text)

    # 账户总结
    try:
        from auto_trader import get_trade_summary
        from position_manager import load_portfolio as _lp
        lines.append("\n" + get_trade_summary(_lp()))
    except Exception:
        pass

    # 板块热度
    if sector_report:
        lines.append(sector_report)

    # 新股追踪
    if ipo_report:
        lines.append(ipo_report)

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ 仅供参考，不构成投资建议")
    lines.append(f"📱 看板：http://{SERVER_IP}:8888")

    return "\n".join(lines)


def daily_run():
    """每日主任务：全量分析 + 报告生成 + 推送"""
    print(f"[{datetime.now()}] 开始每日分析任务...")

    # 同步更新新股观察池（后台静默）
    try:
        from ipo_tracker import update_ipo_watchlist
        wl = update_ipo_watchlist()
        print(f"[新股追踪] 观察池共 {len(wl)} 只新股")
    except Exception as e:
        print(f"[新股追踪] 失败（不影响主流程）: {e}")

    # 全量技术+基本面分析
    data = analyzer.run_analysis(config, use_dynamic=True)

    # 自动模拟交易
    trade_logs = []
    try:
        from auto_trader import auto_trade, get_trade_summary
        from position_manager import load_portfolio
        trade_logs = auto_trade(data)
        if trade_logs:
            print("\n[模拟交易]")
            for log in trade_logs:
                print(f"  {log}")
    except Exception as e:
        print(f"[模拟交易] 失败（不影响报告）: {e}")

    # 生成报告
    report = generate_report(data)
    print("\n" + report)

    # 保存
    os.makedirs("data", exist_ok=True)
    report_path = f"data/report_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[报告] 已保存 {report_path}")

    print(f"[{datetime.now()}] 每日任务完成！")
    return report


if __name__ == "__main__":
    daily_run()
