# 港股分析系统 - 配置文件
import os
from pathlib import Path

# ── 加载 .env 文件 ──
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# ── 环境配置（从 .env 或环境变量读取，不在代码中硬编码）──
WECOM_TARGET = os.environ.get("WECOM_TARGET", "")
SERVER_IP = os.environ.get("SERVER_IP", "127.0.0.1")


# 仓位设置（人民币）
TOTAL_CAPITAL = 100000       # 总资金
MAX_POSITION = 15000         # 单只股票最大仓位
RESERVE_CASH = 20000         # 保留现金（不投资）

# 技术指标参数
RSI_PERIOD = 14
RSI_OVERSOLD = 35            # RSI 低于此值视为超卖（买入信号）
RSI_OVERBOUGHT = 70          # RSI 高于此值视为超买（卖出信号）
MA_SHORT = 10                # 短期均线
MA_LONG = 30                 # 长期均线
VOLUME_SPIKE = 1.5           # 成交量放大倍数阈值

# 风险控制
STOP_LOSS_PCT = 0.08         # 止损线：亏损 8% 触发
TAKE_PROFIT_PCT = 0.15       # 止盈线：盈利 15% 触发

# 仓位管理
MAX_POSITIONS = 99           # 不限制持仓数量
MAX_INVESTED_CNY = 100000    # 总投入上限 = 总资金（不限制）

# ── 调度配置 ──
DAILY_ANALYSIS_TIME = (16, 30)      # 完整分析 + 交易（收盘后30分钟）
INTRADAY_CHECK_TIMES = [            # 盘中持仓检查（仅止损/止盈/跟踪止损）
    (9, 45),   # 开盘15分钟后
    (12, 0),   # 午间休市
    (14, 0),   # 下午盘中
]

# ── 港股假期日历 ──
# 每年更新，来源：https://www.hkex.com.hk/Services/Trading/Trading-Calendar
HK_HOLIDAYS = {
    # 2025
    "2025-01-01",                                   # 元旦
    "2025-01-29", "2025-01-30", "2025-01-31",       # 农历新年
    "2025-04-04",                                   # 清明
    "2025-04-18", "2025-04-21",                     # 耶稣受难+复活节
    "2025-05-01",                                   # 劳动节
    "2025-05-05",                                   # 佛诞
    "2025-05-31",                                   # 端午
    "2025-07-01",                                   # 回归纪念日
    "2025-10-01",                                   # 国庆
    "2025-10-07",                                   # 重阳
    "2025-10-29",                                   # 重阳（补假）
    "2025-12-25", "2025-12-26",                     # 圣诞
    # 2026
    "2026-01-01",                                   # 元旦
    "2026-02-17", "2026-02-18", "2026-02-19",       # 农历新年
    "2026-04-03", "2026-04-06",                     # 耶稣受难+复活节
    "2026-04-05",                                   # 清明
    "2026-05-01",                                   # 劳动节
    "2026-05-24",                                   # 佛诞
    "2026-06-19",                                   # 端午
    "2026-07-01",                                   # 回归纪念日
    "2026-10-01",                                   # 国庆
    "2026-10-19",                                   # 重阳
    "2026-12-25", "2026-12-26",                     # 圣诞
}
