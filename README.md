# 港股量化分析 & 模拟交易系统

基于多因子评分的港股模拟交易系统。每日自动扫描全市场，经技术面、基本面、板块热度、大盘趋势、AI 多模型集成五重筛选后执行交易。

## 快速开始

```bash
# 安装依赖
pip install pandas requests flask beautifulsoup4 lxml codebuddy-agent-sdk

# 配置环境变量
cp .env.example .env   # 编辑 .env 填入 CODEBUDDY_API_KEY、SERVER_IP 等

# 运行
python3.11 daily_report.py     # 手动执行一次完整分析+交易
python3.11 hkstock_cron.py     # 启动定时调度（后台常驻）
python3.11 dashboard.py        # 启动 Web 看板（:8888）
```

> `.env` 已在 `.gitignore` 中，不会被提交。AI 分析需要 `CODEBUDDY_API_KEY`，未配置时自动跳过。

## 系统架构

```
全市场扫描 (stock_screener)        IPO 新股 (ipo_tracker)
        \                               /
         --------> 合并标的池 -------->
                       |
              技术分析 (analyzer)
              RSI / MACD / BB / ADX / 均线 / 成交量
              + 恒生指数趋势检测（牛/熊/中性）
                       |
              基本面过滤 (fundamentals)
              PE / PB / ROE / 港交所公告 / 新闻舆情
                       |
              板块热度加分 (sector_analyzer)
                       |
              AI 集成分析 (ai_analyzer)
              5 模型并行 → 加权投票
                       |
              交易执行 (auto_trader + position_manager)
              买入 / 卖出 / 止损止盈 / 相关性检查
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

### 评分

最终评分由以下维度叠加，>= 7 分触发买入：

```
最终评分 = 技术面 + 基本面 + 公告情绪 + 新闻情绪 + 板块热度 + 大盘调整 + AI集成
```

**技术面（-10 ~ +10）**

| 指标 | 看多 | 看空 |
|------|------|------|
| RSI(14) Wilder EMA | < 35 超卖 +3 | > 70 -2，> 80 -4 |
| 均线 MA10/MA30 | 金叉 +2 | 死叉 -2 |
| MACD | 金叉+零轴上 +2，金叉 +1 | 死叉 -2 |
| 布林带 | 触下轨 +2 | 触上轨 -2 |
| ADX > 25 强趋势 | 顺势 +1 | 逆势 -1 |
| 成交量 > 1.5x | 放量上涨 +1 | 放量下跌 -1 |

**基本面（-5 ~ +5）**

| 指标 | +2 | +1 | -1 | -2 | 一票否决 |
|------|----|----|----|----|----------|
| PE | 5-15 | 15-25 | 40-80 | > 80 | > 200 排除 |
| ROE | > 25% | > 15% | | | |
| PB | < 1 | 1-1.5 | | | < 0 排除 |
| 股息率 | > 5% | > 3% | | | |

另有港交所公告情绪（-3 ~ +3）和新闻舆情（-2 ~ +2）叠加。

**大盘趋势**

拉取恒生指数 MA10/MA30 + RSI：偏空时所有正评分 -2，偏多时不额外加分。

**板块热度**

15 大板块（200+ 关键词匹配），热门板块个股 +1 ~ +2。

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
2. 单只仓位 <= `MAX_POSITION`，一手成本超标则跳过
3. 现金扣除 `RESERVE_CASH` 后充足
4. 与现有持仓 30 天日收益率相关性 < 0.85
5. 同板块已持仓 < 2 只

**卖出**（分级触发）：

| 条件 | 动作 |
|------|------|
| 亏损 >= 8% | 止损清仓 |
| 盈利 >= 15% | 止盈清仓 |
| 盈利 > 10%，跌破最高价 × 95% | 跟踪止损 |
| 盈利 > 5%，跌破成本 × 102% | 保本止损 |
| 评分 <= -5 或出现死叉 | 信号转弱清仓 |
| 评分 <= -3，持仓 >= 3 天 | 减仓 50% |
| 评分 <= -1，持仓 >= 5 天，亏 > 3% | 减仓 50% |

**费用**（真实港股单边费率）：

| 费目 | 费率 |
|------|------|
| 券商佣金 | 0.03%（min 3 HKD） |
| 平台费 | 15 HKD/笔 |
| SFC 征费 | 0.00278% |
| HKEX 交易费 | 0.00565% |
| CCASS 结算费 | 0.002%（min 2 HKD） |
| 印花税 | 0.13%（ceil to 1 HKD） |

HKD/CNY 汇率从 open.er-api.com 实时获取，4 小时缓存。

### 调度

每个交易日自动运行，跳过周末和港股假期（`config.py` 中 `HK_HOLIDAYS`）：

| 时间 | 动作 |
|------|------|
| 09:45 / 12:00 / 14:00 | 盘中持仓检查：止损/止盈/跟踪止损 |
| 16:30 | 完整分析 + 交易 + 报告生成 |

### 配置参数

`config.py` 中可调整的核心参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TOTAL_CAPITAL` | 100,000 | 总资金（CNY） |
| `MAX_POSITION` | 15,000 | 单只最大仓位（CNY） |
| `RESERVE_CASH` | 20,000 | 保留现金（CNY） |
| `STOP_LOSS_PCT` | 8% | 止损线 |
| `TAKE_PROFIT_PCT` | 15% | 止盈线 |
| `RSI_OVERSOLD` | 35 | RSI 超卖阈值 |
| `RSI_OVERBOUGHT` | 70 | RSI 超买阈值 |
| `MA_SHORT` / `MA_LONG` | 10 / 30 | 短期/长期均线周期 |

## 部署

使用 Supervisor 管理后台服务，支持崩溃自动恢复：

```bash
pip install supervisor
```

| 进程 | 脚本 | 说明 |
|------|------|------|
| `hkstock-dashboard` | `dashboard.py` | Web 看板，端口 8888 |
| `hkstock-cron` | `hkstock_cron.py` | 定时调度守护进程 |

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
  config.py               全局配置 + .env 加载 + 港股假期日历
  stock_screener.py        全市场扫描，成交额+波动率筛选 Top 100
  ipo_tracker.py           IPO 新股检测，上市满 15 天注入分析
  analyzer.py              技术分析主引擎 + 恒生指数趋势检测
  indicators.py            指标计算：RSI(Wilder EMA) / MACD / BB / ADX
  fundamentals.py          基本面过滤 + 港交所公告 + 新闻舆情
  sector_analyzer.py       板块分类(15板块 200+关键词) + 热度检测
  ai_analyzer.py           5 模型集成分析，异步并行 + 快速路径
  auto_trader.py           交易引擎：买卖/盘中检查/相关性/净值快照
  position_manager.py      仓位管理：持仓/止损止盈/费用/汇率
  daily_report.py          每日报告生成
  hkstock_cron.py          定时调度守护进程
  dashboard.py             Flask Web 看板
  database.py              SQLite 持久化
  real_data.py             腾讯 API 接口（行情/K线/手数）
  backtest.py              回测引擎（与实盘策略对齐）
  manage.sh                Supervisor 管理脚本
  templates/index.html     看板前端
  data/                    运行时数据（git 忽略）
    portfolio.json         模拟持仓
    latest.json            最新分析结果
    hkstock.db             SQLite 数据库
    ipo_watchlist.json     IPO 观察池
    report_*.txt           历史报告
```

## 注意事项

- 本系统为**模拟交易**，不连接真实券商，不产生实际交易
- 数据来自腾讯免费行情 API，存在 15 分钟左右延迟
- 港股假期日历需每年手动更新（`config.py` 中 `HK_HOLIDAYS`）
- 投资有风险，系统评分仅供参考，不构成投资建议
