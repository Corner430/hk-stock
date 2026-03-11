# 港股量化分析 & 模拟交易系统

自动扫描港股主板全量股票，基于技术指标 + 基本面 + 板块热度 + 大盘趋势 + AI 多模型集成进行综合评分，执行模拟交易并生成每日报告。

## 功能概览

- **全市场扫描** — 覆盖港股主板 ~6000 个代码位，动态筛选 Top 100 活跃标的
- **IPO 新股追踪** — 自动发现上市满 15 天的新股，注入分析管道
- **技术分析** — RSI(Wilder EMA)、MACD、布林带、ADX、均线金叉/死叉、成交量异动
- **基本面过滤** — PE/PB/ROE/股息率 + 港交所公告情绪 + 新闻舆情
- **大盘趋势** — 恒生指数 MA10/MA30 + RSI 判定牛熊，熊市自动下调买入评分
- **板块热度** — 15 大板块 200+ 关键词自动识别，热门板块加分
- **AI 多模型集成** — 5 个大模型并行分析，加权投票 + 一致性共识
- **模拟交易** — 评分 >=7 买入、分级卖出、动态跟踪止损、持仓相关性检查
- **真实费用** — 港股完整费率（券商佣金/平台费/SFC/HKEX/CCASS/印花税）
- **动态汇率** — HKD/CNY 实时汇率，4 小时缓存
- **盘中监控** — 每日 4 次检查（9:45 / 12:00 / 14:00 / 16:30）
- **Web 看板** — Flask + Chart.js 实时展示分析结果和权益曲线

## 系统架构

```
全市场扫描 (stock_screener)          IPO 新股扫描 (ipo_tracker)
        \                                 /
         \                               /
          -----> 合并 watchlist -------->
                       |
              恒生指数趋势检测 (analyzer)
              MA10/MA30 + RSI → 牛/熊/中性
                       |
              技术分析 (analyzer + indicators)
              RSI / MACD / BB / ADX / 均线 / 成交量
                       |
              基本面过滤 (fundamentals)
              PE / PB / ROE / 公告 / 新闻
                       |
              板块热度 (sector_analyzer)
              15 大板块，热门板块加分
                       |
              大盘趋势调整
              熊市：正评分 -2
                       |
              AI 集成分析 (ai_analyzer)
              5 模型并行 → 加权投票
                       |
              模拟交易 (auto_trader)
              买入 / 卖出 / 止损止盈 / 相关性检查
                       |
              每日报告 (daily_report)
              文本报告 + Web 看板 + 数据库
```

## 快速开始

### 环境要求

- Python 3.11+
- 依赖包：`pandas`, `requests`, `flask`, `beautifulsoup4`, `lxml`
- AI 分析需要：`codebuddy-agent-sdk`

### 安装

```bash
pip install pandas requests flask beautifulsoup4 lxml codebuddy-agent-sdk
```

### 配置

复制 `.env.example` 为 `.env`，填入你的配置：

```bash
cp .env.example .env
```

`.env` 文件内容：

```
CODEBUDDY_API_KEY=your_api_key_here
WECOM_TARGET=your_wecom_target_id
SERVER_IP=your_server_ip
```

> `.env` 已在 `.gitignore` 中，不会被提交到 git。

### 运行

```bash
# 手动执行一次完整分析
python3.11 daily_report.py

# 启动定时调度守护进程
python3.11 hkstock_cron.py

# 启动 Web 看板（端口 8888）
python3.11 dashboard.py
```

## 策略详解

### 评分体系

最终评分由多个维度叠加，评分 >= 7 才触发买入：

```
最终评分 = 技术面[-10,+10] + 基本面[-5,+5] + 公告情绪[-3,+3]
         + 新闻情绪[-2,+2] + 板块加分[0,+2] + 大盘调整[0,-2]
         + AI集成[-3,+3]
```

#### 技术面评分（-10 ~ +10）

| 指标 | 加分 | 减分 |
|------|------|------|
| RSI(14) Wilder EMA | < 35 超卖 +3 | > 70 超买 -2，> 80 -4 |
| 均线 MA10/MA30 | 金叉 +2 | 死叉 -2 |
| MACD | 金叉+零轴上 +2，金叉 +1 | 死叉 -2 |
| 布林带 | 触下轨 +2 | 触上轨 -2 |
| ADX | > 25 强趋势顺势 +1 | > 25 强趋势逆势 -1 |
| 成交量 | 放量上涨 +1 | 放量下跌 -1 |

#### 基本面评分（-5 ~ +5）

- PE 5-15 +2 / 15-25 +1 / 40-80 -1 / >80 -2
- ROE >25% +2 / >15% +1
- PB <1 +2 / 1-1.5 +1
- 高息 >5% +2 / >3% +1
- PE >200 或 PB <0 → 一票否决排除

#### AI 多模型集成（-3 ~ +3）

| 模型 | 权重 |
|------|------|
| Claude-Opus-4.6 | 1.5 |
| GPT-5.2 | 1.3 |
| Gemini-3.1-Pro | 1.3 |
| Claude-Sonnet-4.6 | 1.2 |
| DeepSeek-V3.1 | 1.0 |

快速路径：3 个模型返回后额外等 30 秒截止。一致性 < 50% 调整分减半。

### 交易规则

#### 买入条件（全部满足）

1. 综合评分 >= 7
2. 单只仓位 <= MAX_POSITION（一手超标则跳过）
3. 现金扣除 RESERVE_CASH 后充足
4. 与现有持仓价格相关性 < 0.85（30 天日收益率）
5. 同板块持仓 < 2 只
6. 使用腾讯 API 真实每手股数

#### 卖出条件（分级触发）

| 条件 | 动作 |
|------|------|
| 亏损 >= 8% | 止损清仓 |
| 盈利 >= 15% | 止盈清仓 |
| 盈利 > 10% 且跌破最高价 × 95% | 跟踪止损 |
| 盈利 > 5% 且跌破成本 × 102% | 保本止损 |
| 评分 <= -5 或出现死叉 | 信号转弱清仓 |
| 评分 <= -3 且持仓 >= 3 天 | 减仓 50% |
| 评分 <= -1 且持仓 >= 5 天且亏 > 3% | 减仓 50% |

### 交易费用（真实港股费率）

| 费目 | 费率 |
|------|------|
| 券商佣金 | 0.03%（最低 3 HKD） |
| 平台费 | 15 HKD/笔 |
| SFC 征费 | 0.00278% |
| HKEX 交易费 | 0.00565% |
| CCASS 结算费 | 0.002%（最低 2 HKD） |
| 印花税 | 0.13%（向上取整到 1 HKD） |

## 模块说明

| 模块 | 职责 |
|------|------|
| `stock_screener.py` | 全市场扫描，按成交额 + 波动率筛选 Top 100 活跃标的 |
| `ipo_tracker.py` | IPO 新股检测，上市满 15 天自动注入分析管道 |
| `analyzer.py` | 技术分析主引擎 + 恒生指数趋势检测，计算评分并生成买卖信号 |
| `indicators.py` | 技术指标计算（RSI Wilder EMA、MACD、布林带、ADX） |
| `fundamentals.py` | 基本面分析 + 港交所公告 + 新闻舆情 |
| `sector_analyzer.py` | 板块分类（15 大板块 200+ 关键词）+ 热度检测 |
| `ai_analyzer.py` | 5 模型集成 AI 分析，异步并行 + 快速路径机制 |
| `auto_trader.py` | 交易引擎：买入/卖出/盘中检查/相关性检查/净值快照 |
| `position_manager.py` | 仓位管理：持仓读写/止损止盈检测/交易费用/汇率/仓位限制 |
| `config.py` | 全局配置 + .env 加载 + 港股假期日历 |
| `daily_report.py` | 每日报告生成 + 推送 |
| `hkstock_cron.py` | 多时段定时调度守护进程 |
| `dashboard.py` | Flask Web 看板 |
| `database.py` | SQLite 数据持久化 |
| `real_data.py` | 腾讯股票 API 数据接口（行情 + 历史 K 线 + 真实手数） |
| `backtest.py` | 回测引擎（与实盘策略对齐） |

## 调度机制

系统每个交易日运行 4 次，自动跳过周末和港股假期：

| 时间 | 类型 | 动作 |
|------|------|------|
| 09:45 | 盘中检查 | 检查持仓止损/止盈/跟踪止损 |
| 12:00 | 盘中检查 | 检查持仓止损/止盈/跟踪止损 |
| 14:00 | 盘中检查 | 检查持仓止损/止盈/跟踪止损 |
| 16:30 | 完整分析 | 全市场扫描 + 分析 + 交易 + 报告 |

## 关键配置参数

在 `config.py` 中可调整：

```python
# 资金管理
TOTAL_CAPITAL = 100000       # 总资金（CNY）
MAX_POSITION = 15000         # 单只最大仓位
RESERVE_CASH = 20000         # 保留现金

# 风险控制
STOP_LOSS_PCT = 0.08         # 止损线 -8%
TAKE_PROFIT_PCT = 0.15       # 止盈线 +15%

# 技术指标
RSI_OVERSOLD = 35            # RSI 超卖
RSI_OVERBOUGHT = 70          # RSI 超买
MA_SHORT = 10                # 短期均线
MA_LONG = 30                 # 长期均线
```

## 部署 & 进程管理

### Supervisor

系统使用 **Supervisor** 管理后台服务，支持崩溃自动恢复和开机自启动。

```bash
pip install supervisor
```

托管的进程：

| 进程名 | 对应脚本 | 说明 |
|--------|----------|------|
| `hkstock-dashboard` | `dashboard.py` | Web 看板，监听 8888 端口 |
| `hkstock-cron` | `hkstock_cron.py` | 定时调度守护进程 |

#### 管理命令

```bash
./manage.sh status       # 查看服务状态
./manage.sh start        # 启动所有服务
./manage.sh stop         # 停止所有服务
./manage.sh restart      # 重启所有服务
./manage.sh log-dash     # 查看看板日志
./manage.sh log-cron     # 查看定时任务日志
```

## 项目结构

```
hk-stock/
  .env                  # 敏感配置（git 忽略）
  .gitignore
  config.py             # 全局配置 + .env 加载
  stock_screener.py     # 全市场扫描
  ipo_tracker.py        # IPO 新股追踪
  analyzer.py           # 技术分析 + 大盘趋势
  indicators.py         # 指标计算（Wilder EMA）
  fundamentals.py       # 基本面 + 公告 + 新闻
  sector_analyzer.py    # 板块识别 + 热度
  ai_analyzer.py        # AI 多模型集成
  auto_trader.py        # 交易引擎 + 盘中检查
  position_manager.py   # 仓位 + 止损止盈 + 费用 + 汇率
  daily_report.py       # 每日报告
  hkstock_cron.py       # 定时调度
  dashboard.py          # Web 看板
  database.py           # SQLite 存储
  real_data.py          # 数据接口
  backtest.py           # 回测
  manage.sh             # 进程管理脚本
  templates/
    index.html          # 看板前端
  data/                 # 运行时数据（git 忽略）
    latest.json         # 最新分析结果
    portfolio.json      # 模拟持仓
    hkstock.db          # SQLite 数据库
    ipo_watchlist.json   # IPO 观察池
    report_*.txt        # 历史报告
```

## 注意事项

- 本系统为**模拟交易**，不连接真实券商，不产生实际交易
- 股票数据来自腾讯免费行情 API，可能存在延迟
- AI 分析需要 CodeBuddy API Key，未配置时会自动跳过
- 港股假期日历需每年手动更新（见 `config.py` 中 `HK_HOLIDAYS`）
- 投资有风险，系统评分仅供参考，不构成投资建议
