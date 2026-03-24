"""
共享类型定义

项目中核心数据结构的 TypedDict，用于函数签名注解和 IDE 提示。
"""
from __future__ import annotations

from typing import Any, TypedDict, NotRequired


# ── 分析结果 ─────────────────────────────────────────────────

class StockAnalysis(TypedDict):
    """单只股票的分析结果（analyze_stock 产出 + 后续 enrichment）"""
    # 基础字段
    ticker: str
    date: str
    price: float
    prev_close: float
    change_pct: float
    volume: int
    avg_volume: int
    rsi: float
    ma_short: float
    ma_long: float
    macd: float
    macd_signal: float
    bb_upper: float
    bb_lower: float
    adx: float
    volume_ratio: float
    score: int
    action: str
    signals: list[str]
    suggested_position_cny: int
    name: str
    # 可选字段（根据数据可用性和处理阶段）
    momentum: NotRequired[float]
    atr: NotRequired[float]
    fundamentals: NotRequired[dict[str, Any]]
    fundamental_notes: NotRequired[list[str]]
    announcement_notes: NotRequired[list[str]]
    market_sentiment: NotRequired[str]
    ai_analysis: NotRequired[dict[str, Any]]
    ai_score_adj: NotRequired[int]
    tc_code: NotRequired[str]
    amount_hkd: NotRequired[float]


class AnalysisSummary(TypedDict):
    """分析摘要统计"""
    total_analyzed: int
    buy_signals: int
    sell_signals: int
    hold_signals: int


class AnalysisOutput(TypedDict):
    """run_analysis() 完整输出"""
    generated_at: str
    is_real_data: bool
    stocks: list[StockAnalysis]
    realtime: dict[str, dict[str, float]]
    summary: AnalysisSummary
    sector_report: str
    market_regime: str
    market_signals: dict[str, Any]
    position_multiplier: float


# ── 实时行情 ─────────────────────────────────────────────────

class RealtimeQuote(TypedDict):
    """单只股票的实时行情"""
    name: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    volume: int
    lot_size: int
    updated_at: str


# ── 组合与交易 ───────────────────────────────────────────────

class Position(TypedDict):
    """持仓记录"""
    name: str
    shares: int
    avg_cost_hkd: float
    total_cost_cny: float
    lot_size: NotRequired[int]
    high_watermark_hkd: NotRequired[float]
    tp_executed: NotRequired[list[int]]
    pending_build: NotRequired[dict[str, Any]]


class TradeRecord(TypedDict, total=False):
    """交易记录（BUY 和 SELL 共用，部分字段仅在特定操作时存在）"""
    # 共有字段
    date: str
    action: str       # "BUY" | "SELL"
    ticker: str
    name: str
    shares: int
    price_hkd: float
    reason: str
    # BUY 专有
    cost_cny: float
    # SELL 专有
    revenue_cny: float
    pnl_cny: float
    pnl_pct: float


class DailySnapshot(TypedDict):
    """每日资产快照"""
    date: str
    total_value_cny: float
    cash_cny: float
    position_value_cny: float
    return_pct: float
    total_return_cny: float
    n_positions: int


class Portfolio(TypedDict):
    """完整组合状态"""
    total_capital_cny: float
    cash_cny: float
    positions: dict[str, Position]
    trades: list[TradeRecord]
    daily_snapshots: list[DailySnapshot]
    created_at: str


# ── 止损止盈告警 ─────────────────────────────────────────────

class StopLossAlert(TypedDict):
    """止损/止盈检测结果"""
    ticker: str
    name: str
    action: str       # "止损" | "跟踪止盈" | "止盈" | "时间止损" | "分批止盈(i/n)"
    pnl_pct: float
    pnl_cny: float
    current_price: float
    avg_cost: float
    hold_days: int
    partial_shares: NotRequired[int]
    tp_level_index: NotRequired[int]
