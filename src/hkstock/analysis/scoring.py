"""
统一评分基础设施

提供评分数据结构、分值映射和钳位函数。
各模块（analyzer, fundamentals, sector, ai_analyzer）使用这些
共享定义来确保评分逻辑一致性。
"""
from dataclasses import dataclass, field


# ── 评分边界常量 ──────────────────────────────────────────────
SCORE_MIN = -10
SCORE_MAX = 10

# 基本面评分边界
FUNDAMENTAL_SCORE_MIN = -5
FUNDAMENTAL_SCORE_MAX = 5

# 公告情绪评分边界
ANNOUNCEMENT_SCORE_MIN = -3
ANNOUNCEMENT_SCORE_MAX = 3

# 新闻情绪评分边界
NEWS_SCORE_MIN = -2
NEWS_SCORE_MAX = 2

# AI 调整评分边界
AI_SCORE_MIN = -3
AI_SCORE_MAX = 3

# 板块热度评分边界（由 config 中 SECTOR_HEAT_BOOST_MAX / SECTOR_COLD_PENALTY_MAX 控制）
SECTOR_SCORE_MIN = -2
SECTOR_SCORE_MAX = 3

# 大盘趋势调整
REGIME_BEARISH_PENALTY = -2


@dataclass
class ScoreBreakdown:
    """
    单只股票的评分分解，记录各维度贡献。

    用于审计和回溯评分来源：
        breakdown = ScoreBreakdown(technical=5, fundamental=2, sector_heat=1)
        print(breakdown.total)  # 8
    """
    technical: int = 0        # 技术面 (-10 ~ +10)
    momentum: int = 0         # 动量因子 (-2 ~ +2)
    fundamental: int = 0      # 基本面 (-5 ~ +5)
    announcement: int = 0     # 港交所公告情绪 (-3 ~ +3)
    news: int = 0             # 新闻舆情 (-3 ~ +3)
    sector_heat: int = 0      # 板块热度 (-2 ~ +3)
    market_regime: int = 0    # 大盘趋势调整 (-2 ~ 0)
    ai_adjustment: int = 0    # AI 集成调整 (-3 ~ +3)

    @property
    def total(self) -> int:
        """各维度合计（未钳位）"""
        return (self.technical + self.momentum + self.fundamental
                + self.announcement + self.news + self.sector_heat
                + self.market_regime + self.ai_adjustment)

    @property
    def clamped_total(self) -> int:
        """钳位到 [SCORE_MIN, SCORE_MAX] 的最终评分"""
        return clamp_score(self.total)

    def to_dict(self) -> dict:
        """序列化为字典（用于 JSON 输出）"""
        return {
            "technical": self.technical,
            "momentum": self.momentum,
            "fundamental": self.fundamental,
            "announcement": self.announcement,
            "news": self.news,
            "sector_heat": self.sector_heat,
            "market_regime": self.market_regime,
            "ai_adjustment": self.ai_adjustment,
            "total": self.total,
            "clamped_total": self.clamped_total,
        }


def clamp_score(score: int, lo: int = SCORE_MIN, hi: int = SCORE_MAX) -> int:
    """将评分钳位到指定范围"""
    return max(lo, min(hi, score))


def score_to_action(score: int) -> str:
    """
    评分 → 操作建议映射（与 analyzer.py 保持一致）

    >= 6: 强烈买入
    >= 4: 买入
    >= 3: 试探性买入
    <= -4: 考虑卖出
    <= -1: 观望/减仓
    else:  持有观望
    """
    if score >= 6:
        return "强烈买入"
    elif score >= 4:
        return "买入"
    elif score >= 3:
        return "试探性买入"
    elif score <= -4:
        return "考虑卖出"
    elif score <= -1:
        return "观望/减仓"
    else:
        return "持有观望"


def score_to_position_pct(score: int) -> float:
    """
    评分 → 建议仓位比例映射

    >= 6: 100%
    >= 4: 70%
    >= 3: 40%
    else:  0%
    """
    if score >= 6:
        return 1.0
    elif score >= 4:
        return 0.7
    elif score >= 3:
        return 0.4
    else:
        return 0.0
