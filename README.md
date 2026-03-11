# 港股量化分析 & 模拟交易系统

基于多因子评分的港股模拟交易系统。每日自动扫描全市场，经技术面、基本面、市场数据、板块热度、大盘趋势、AI 多模型集成六重筛选后执行交易。

## 快速开始

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖（自动创建虚拟环境）
uv sync

# 配置环境变量
cp .env.example .env   # 编辑 .env 填入 CODEBUDDY_API_KEY、SERVER_IP 等

# 运行
uv run python daily_report.py     # 手动执行一次完整分析+交易
uv run python hkstock_cron.py     # 启动定时调度（后台常驻）
uv run python dashboard.py        # 启动 Web 看板（:8888）
```

> `.env` 已在 `.gitignore` 中，不会被提交。AI 分析需要 `CODEBUDDY_API_KEY`，未配置时自动跳过。

## 系统架构

```
全市场扫描 (stock_screener)        IPO 新股 (ipo_tracker)
        \                               /
         --------> 合并标的池 -------->
                       |
              技术分析 (analyzer)
              RSI / MACD / BB / ADX / 均线 / 成交量 / 动量
              + 量价背离检测
              + 恒生指数趋势检测（牛/熊/中性）
                       |
              市场数据 (market_data)         <-- 新增
              南向资金 / AH溢价 / VHSI / 卖空 / 美股隔夜 / MSCI
              → 仓位倍数 0.3x ~ 1.2x
                       |
              基本面过滤 (fundamentals)
              PE(行业相对) / PB / ROE / PEG / FCF / 港交所公告 / 新闻舆情
                       |
              板块热度加分/减分 (sector_analyzer)
              热门 +1~+3 / 冷门 -1~-2
                       |
              AI 集成分析 (ai_analyzer)
              5 模型并行 → 加权投票
                       |
              交易执行 (auto_trader + position_manager)
              分批建仓 / 分批止盈 / 回撤保护 / ATR仓位 / 碎股处理
                       |
              报告 + 持久化 (daily_report + database)
```

## 策略详解

### 选股

每次分析从零开始，不使用预设股票列表：

1. 扫描港股主板全量代码（00001-04999、06xxx、09xxx），约 6000 个代码位
2. 初筛：成交额 >= 5000 万 HKD，股价 >= 1.0 HKD
3. 精筛：拉 60 天历史，过滤低波动（< 0.8%）和游资爆炒（近 5 日成交额 > 长期 10 倍）
4. 按活跃度评分（成交额 70% + 波动率 30%）取 Top 100
5. 上市满 15 天的 IPO 新股自动注入
6. 自由流通比例 < 15% 的股票在基本面阶段排除

### 评分

最终评分由以下维度叠加，>= 7 分触发买入：

```
最终评分 = 技术面 + 动量 + 基本面 + 公告情绪 + 新闻情绪 + 板块热度 + 大盘调整 + 市场数据 + AI集成
```

**技术面（-10 ~ +10）**

| 指标 | 看多 | 看空 |
|------|------|------|
| RSI(14) Wilder EMA | < 35 超卖 +3 | > 70 -2，> 80 -4 |
| 均线 MA10/MA30 | 金叉 +2 | 死叉 -2 |
| MACD | 金叉+零轴上 +2，金叉 +1 | 死叉 -2 |
| 布林带 | 触下轨 +2 | 触上轨 -2 |
| ADX > 25 强趋势 | 顺势 +1 | 逆势 -1 |
| 成交量 > 1.5x | 放量上涨 +2 | 放量下跌 -2 |
| 量价背离 | 缩量下跌（抛压减弱）+1 | 价涨量缩（动能不足）-1 |
| 动量(20日) | > 10% +2，> 5% +1 | < -10% -1 |

**基本面（-5 ~ +5）**

| 指标 | +2 | +1 | -1 | -2 | 一票否决 |
|------|----|----|----|----|----------|
| PE（行业相对） | < 行业 0.5x | < 行业 0.8x | > 行业 1.5x | > 行业 2.0x | > 200 排除 |
| ROE | > 25% | > 15% | < 5% | | |
| PB | < 1 | 1-1.5 | | | < 0 排除 |
| 股息率 | > 5% | > 3% | | | |
| PEG | < 0.5 | < 1.0 | | > 3.0 | |
| FCF Yield | > 8% | > 5% | < 0 | | |

另有港交所公告情绪（-3 ~ +3）和新闻舆情（-2 ~ +2）叠加。

**大盘趋势**

拉取恒生指数 MA10/MA30 + RSI：偏空时所有正评分 -2，偏多时不额外加分。

**市场数据信号**

南向资金、AH溢价指数、VHSI波动率、卖空比率、美股隔夜 → 综合打分 → 仓位倍数（bearish 时仅用 30% 仓位，bullish 时可到 120%）。MSCI 调仓窗口期额外减分。

**板块热度**

15 大板块（200+ 关键词匹配），热门板块个股 +1 ~ +3（按涨幅分级），冷门板块 -1 ~ -2。

**AI 多模型集成（-3 ~ +3）**

| 模型 | 权重 |
|------|------|
| Claude-Opus-4.6 | 1.5 |
| GPT-5.2 | 1.3 |
| Gemini-3.1-Pro | 1.3 |
| Claude-Sonnet-4.6 | 1.2 |
| DeepSeek-V3.1 | 1.0 |

5 个模型并行调用，3 个返回后额外等 30 秒截止。加权平均分 1-10 映射为 -3 ~ +3 调整分。模型一致性 < 50% 时调整分减半。

### 交易规则

**买入**（以下条件全部满足）：

1. 综合评分 >= 7
2. 组合回撤未超过停买线（10%）
3. 单只仓位 <= `MAX_POSITION`，且不超过 ATR 风控仓位
4. 仓位受市场信号倍数调节（0.3x ~ 1.2x）
5. 现金扣除 `RESERVE_CASH` 后充足
6. 与现有持仓 30 天日收益率相关性 < 0.85
7. 同板块已持仓 < 2 只
8. 分批建仓：首批 30%，后续 30% + 40%（记录待建仓计划）

**卖出**（分级触发）：

| 条件 | 动作 |
|------|------|
| 亏损 >= 8% | 止损清仓 |
| 盈利 >= 10% | 分批止盈：卖出 1/3 |
| 盈利 >= 20% | 分批止盈：再卖 1/3 |
| 盈利 >= 30% | 分批止盈：清仓 |
| 盈利 > 10%，跌破最高价 x 95% | 跟踪止损 |
| 盈利 > 5%，跌破成本 x 102% | 保本止损 |
| 持仓 >= 20 天，涨幅 < 2% | 时间止损 |
| 组合回撤 >= 15% | 强制减仓 50%（全部持仓） |
| 评分 <= -5 或出现死叉 | 信号转弱清仓 |
| 评分 <= -3，持仓 >= 3 天 | 减仓 50% |
| 评分 <= -1，持仓 >= 5 天，亏 > 3% | 减仓 50% |

卖出时自动处理碎股：如剩余不足一手则全部卖出。

**组合回撤保护**：

| 回撤幅度 | 动作 |
|----------|------|
| >= 5% | 发出警告 |
| >= 10% | 停止新开仓 |
| >= 15% | 强制全部持仓减仓 50% |

**费用**（真实港股单边费率）：

| 费目 | 费率 |
|------|------|
| 券商佣金 | 0.03%（min 3 HKD） |
| 平台费 | 15 HKD/笔 |
| SFC 征费 | 0.00278% |
| HKEX 交易费 | 0.00565% |
| CCASS 结算费 | 0.002%（min 2 HKD） |
| 印花税 | 0.1%（ceil to 1 HKD）[2024 年起] |

HKD/CNY 汇率从 open.er-api.com 实时获取，4 小时缓存。

### 调度

每个交易日自动运行，跳过周末和港股假期（`config.py` 中 `HK_HOLIDAYS`）：

| 时间 | 动作 |
|------|------|
| 09:45 / 12:00 / 14:00 | 盘中持仓检查：止损/止盈/跟踪止损/时间止损/分批止盈 |
| 16:30 | 完整分析 + 交易 + 报告生成 |

### 回测

```bash
uv run python backtest.py              # 完整回测（默认）
uv run python backtest.py weekly       # 最近一周
uv run python backtest.py multiwindow  # 多区间（防过拟合）
```

回测引擎与实盘策略对齐（含动量/量价背离信号），输出 Sharpe Ratio、Calmar Ratio、最大回撤、年化收益/波动率等风险指标，包含滑点模拟（单边 0.05%）。回测结果标注生存者偏差警告。

### 配置参数

`config.py` 中可调整的核心参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TOTAL_CAPITAL` | 100,000 | 总资金（CNY） |
| `MAX_POSITION` | 15,000 | 单只最大仓位（CNY） |
| `MAX_POSITIONS` | 10 | 最多同时持仓股票数 |
| `RESERVE_CASH` | 20,000 | 保留现金（CNY） |
| `STOP_LOSS_PCT` | 8% | 止损线 |
| `TAKE_PROFIT_PCT` | 15% | 止盈线（触发跟踪止损） |
| `DRAWDOWN_WARN_PCT` | 5% | 组合回撤警告线 |
| `DRAWDOWN_HALT_PCT` | 10% | 组合回撤停买线 |
| `DRAWDOWN_REDUCE_PCT` | 15% | 组合回撤强制减仓线 |
| `TIME_STOP_DAYS` | 20 | 时间止损天数 |
| `ATR_RISK_PER_TRADE` | 2% | ATR 单笔风险比例 |
| `PARTIAL_BUILD_RATIOS` | 30/30/40 | 分批建仓比例 |
| `PARTIAL_TP_LEVELS` | 10%/20%/30% | 分批止盈档位 |
| `RSI_OVERSOLD` | 35 | RSI 超卖阈值 |
| `RSI_OVERBOUGHT` | 70 | RSI 超买阈值 |
| `MA_SHORT` / `MA_LONG` | 10 / 30 | 短期/长期均线周期 |
| `MOMENTUM_PERIOD` | 20 | 动量计算周期（交易日） |
| `MIN_FREE_FLOAT_PCT` | 15% | 最低自由流通比例 |

## 部署

使用 Supervisor 管理后台服务，支持崩溃自动恢复：

```bash
# 安装 supervisor（系统级，不通过 uv）
pip install supervisor

# supervisor 配置文件位于 /etc/supervisor/conf.d/hkstock.conf
# 两个进程均通过 uv run 启动，自动使用项目虚拟环境
```

| 进程 | 命令 | 说明 |
|------|------|------|
| `hkstock-dashboard` | `uv run python dashboard.py` | Web 看板，端口 8888 |
| `hkstock-cron` | `uv run python hkstock_cron.py` | 定时调度守护进程 |

```bash
./manage.sh status       # 查看状态
./manage.sh start        # 启动
./manage.sh stop         # 停止
./manage.sh restart      # 重启
./manage.sh log-dash     # 看板日志
./manage.sh log-cron     # 调度日志
```

## 项目结构

```
hk-stock/
  pyproject.toml            项目配置 + 依赖声明（uv 管理）
  uv.lock                   依赖锁文件
  config.py                 全局配置 + .env 加载 + 港股假期日历
  stock_screener.py         全市场扫描，成交额+波动率筛选 Top 100
  ipo_tracker.py            IPO 新股检测，上市满 15 天注入分析
  analyzer.py               技术分析主引擎 + 大盘趋势 + 市场数据集成
  indicators.py             指标计算：RSI / MACD / BB / ADX / ATR / 动量
  market_data.py            市场级数据：南向资金/AH溢价/VHSI/卖空/MSCI
  fundamentals.py           基本面过滤(PE/PEG/FCF) + 港交所公告 + 新闻舆情
  sector_analyzer.py        板块分类(15板块 200+关键词) + 热度/冷门检测
  ai_analyzer.py            5 模型集成分析，异步并行 + 快速路径
  auto_trader.py            交易引擎：分批建仓/回撤保护/碎股处理/净值快照
  position_manager.py       仓位管理：持仓/三级止损止盈/分批止盈/费用/汇率
  daily_report.py           每日报告生成
  hkstock_cron.py           定时调度守护进程
  dashboard.py              Flask Web 看板（需认证）
  database.py               SQLite 持久化
  real_data.py              腾讯 API 接口（行情/K线/手数）
  backtest.py               回测引擎（Sharpe/Calmar/滑点/偏差警告）
  manage.sh                 Supervisor 管理脚本
  templates/index.html      看板前端
  data/                     运行时数据（git 忽略）
    portfolio.json           模拟持仓
    latest.json              最新分析结果
    hkstock.db               SQLite 数据库
    ipo_watchlist.json       IPO 观察池
    report_*.txt             历史报告
```

## 注意事项

- 本系统为**模拟交易**，不连接真实券商，不产生实际交易
- 数据来自腾讯免费行情 API，存在 15 分钟左右延迟
- 市场数据（南向资金等）来自东方财富 API，网络异常时自动降级
- 港股假期日历需每年手动更新（`config.py` 中 `HK_HOLIDAYS`）
- 回测结果存在生存者偏差，实际表现可能不如回测
- 投资有风险，系统评分仅供参考，不构成投资建议
