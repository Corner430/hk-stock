# 港股量化分析 & 模拟交易系统

自动扫描港股主板全量股票，基于技术指标 + 基本面 + AI 多模型集成进行综合评分，执行模拟交易并生成每日报告。

## 功能概览

- **全市场扫描** — 覆盖港股主板 ~6000 个代码位，动态筛选 Top 100 活跃标的
- **IPO 新股追踪** — 自动发现上市满 15 天的新股，注入分析管道
- **技术分析** — RSI、MACD、布林带、ADX、均线金叉/死叉、成交量异动
- **基本面过滤** — PE/PB/ROE/股息率 + 港交所公告情绪 + 新闻舆情
- **板块热度** — 自动识别股票所属板块，热门板块加分
- **AI 多模型集成** — 5 个大模型并行分析，加权投票 + 一致性共识
- **模拟交易** — 三档买入（试探/标准/强烈）、分级卖出、止损止盈
- **盘中监控** — 每日 4 次检查（9:45 / 12:00 / 14:00 / 16:30）
- **Web 看板** — Flask + Chart.js 实时展示分析结果和权益曲线

## 系统架构

```
全市场扫描 (stock_screener)          IPO 新股扫描 (ipo_tracker)
        \                                 /
         \                               /
          -----> 合并 watchlist -------->
                       |
              技术分析 (analyzer)
              RSI / MACD / BB / ADX
                       |
              基本面过滤 (fundamentals)
              PE / PB / 公告 / 新闻
                       |
              板块热度 (sector_analyzer)
                       |
              AI 集成分析 (ai_analyzer)
              5 模型并行 → 加权投票
                       |
              模拟交易 (auto_trader)
              买入 / 卖出 / 止损止盈
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

### 部署 & 进程管理

系统使用 **Supervisor** 管理后台服务，支持崩溃自动恢复和开机自启动。

```bash
# 安装 supervisor
pip install supervisor
```

Supervisor 配置文件：

| 文件 | 说明 |
|------|------|
| `/etc/supervisor/supervisord.conf` | Supervisor 主配置 |
| `/etc/supervisor/conf.d/hkstock.conf` | 港股服务进程配置 |

托管的进程：

| 进程名 | 对应脚本 | 说明 |
|--------|----------|------|
| `hkstock-dashboard` | `dashboard.py` | Web 看板，监听 8888 端口 |
| `hkstock-cron` | `hkstock_cron.py` | 定时调度守护进程 |

关键特性：
- `autorestart=true` — 进程崩溃后自动重启
- `startretries=5` — 启动失败最多重试 5 次
- 日志自动轮转，单文件最大 10MB，保留 3 份

开机自启动（双重保障）：
- `crontab @reboot` — 系统启动时自动拉起 supervisord
- `.bashrc` — 登录 shell 时检测并补救

#### 管理命令

通过 `manage.sh` 或直接使用 `supervisorctl`：

```bash
./manage.sh status       # 查看服务状态
./manage.sh start        # 启动所有服务
./manage.sh stop         # 停止所有服务
./manage.sh restart      # 重启所有服务
./manage.sh log-dash     # 查看看板日志（最近 50 行）
./manage.sh log-cron     # 查看定时任务日志（最近 50 行）
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `stock_screener.py` | 全市场扫描，按成交额 + 波动率筛选 Top 100 活跃标的 |
| `ipo_tracker.py` | IPO 新股检测，上市满 15 天自动注入分析管道 |
| `analyzer.py` | 技术分析主引擎，计算评分并生成买卖信号 |
| `indicators.py` | 技术指标计算（RSI、MACD、布林带、ADX） |
| `fundamentals.py` | 基本面分析 + 港交所公告 + 新闻舆情 |
| `sector_analyzer.py` | 板块分类（映射表 + 名称关键词自动识别）+ 热度检测 |
| `ai_analyzer.py` | 5 模型集成 AI 分析，异步并行 + 快速路径机制 |
| `auto_trader.py` | 模拟交易引擎，三档买入 / 分级卖出 |
| `position_manager.py` | 仓位管理、止损止盈、跟踪止损、盘中持仓检查 |
| `config.py` | 全局配置 + .env 加载 + 港股假期日历 |
| `daily_report.py` | 每日报告生成 + 推送 |
| `hkstock_cron.py` | 多时段定时调度守护进程 |
| `dashboard.py` | Flask Web 看板 |
| `database.py` | SQLite 数据持久化 |
| `real_data.py` | 腾讯股票 API 数据接口 |
| `backtest.py` | 回测引擎 |

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

## 项目结构

```
hk-stock/
  .env                  # 敏感配置（git 忽略）
  .gitignore
  config.py             # 全局配置 + .env 加载
  stock_screener.py     # 全市场扫描
  ipo_tracker.py        # IPO 新股追踪
  analyzer.py           # 技术分析主引擎
  indicators.py         # 指标计算
  fundamentals.py       # 基本面 + 公告 + 新闻
  sector_analyzer.py    # 板块识别 + 热度
  ai_analyzer.py        # AI 多模型集成
  auto_trader.py        # 模拟交易引擎
  position_manager.py   # 仓位 + 止损止盈
  daily_report.py       # 每日报告
  hkstock_cron.py       # 定时调度
  dashboard.py          # Web 看板
  database.py           # SQLite 存储
  real_data.py          # 数据接口
  backtest.py           # 回测
  manage.sh             # 进程管理脚本（supervisorctl 封装）
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
