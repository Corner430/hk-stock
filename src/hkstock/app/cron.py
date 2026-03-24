"""
港股定时任务守护进程 v2
- 每天 9:45, 12:00, 14:00: 盘中持仓检查（止损/止盈/跟踪止损）
- 每天 16:30: 完整分析 + 交易 + 推送
- 跳过周末和港股假期
- 由 supervisord 管理
"""
import time
import subprocess
import sys
import os
from datetime import datetime
from hkstock.core import config
from hkstock.core.config import PROJECT_ROOT


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def is_trading_day():
    """今天是否为交易日（工作日 + 非港股假期）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    today_str = now.strftime("%Y-%m-%d")
    return today_str not in config.HK_HOLIDAYS


def run_intraday_check():
    """盘中持仓检查 — 仅止损/止盈/跟踪止损，轻量级"""
    log("盘中持仓检查开始...")
    try:
        # 在子进程中执行，避免主进程模块状态污染
        result = subprocess.run(
            [sys.executable, "-c",
             "from hkstock.trading.auto_trader import run_intraday_check; "
             "logs = run_intraday_check(); "
             "[print(l) for l in logs]"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    log(f"  {line.strip()}")
            log("盘中检查完成")
        else:
            log(f"盘中检查失败: {result.stderr[-300:]}")
    except subprocess.TimeoutExpired:
        log("盘中检查超时（>2分钟）")
    except Exception as e:
        log(f"盘中检查异常: {e}")


def run_daily_analysis():
    """完整分析 + 交易 + 推送（收盘后运行）"""
    log("开始每日完整分析...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hkstock.app.daily_report"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            log("每日分析完成")
        else:
            log(f"分析失败: {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        log("分析超时（>10分钟）")
    except Exception as e:
        log(f"分析异常: {e}")


# 跟踪今日已执行的会话（避免重复执行）
sessions_run_today = set()


def main():
    global sessions_run_today
    log("港股调度器 v2 启动（多时段监控）")

    # 构建完整调度表
    schedule = []
    for h, m in config.INTRADAY_CHECK_TIMES:
        schedule.append((h, m, "intraday"))
    ah, am = config.DAILY_ANALYSIS_TIME
    schedule.append((ah, am, "daily"))
    schedule.sort()  # 按时间排序

    times_str = ", ".join([f"{h:02d}:{m:02d}({'盘中' if t=='intraday' else '完整'})" for h, m, t in schedule])
    log(f"调度时间: {times_str}")

    while True:
        now = datetime.now()
        today_str = str(now.date())

        # 每日凌晨重置会话记录
        if sessions_run_today:
            any_key = next(iter(sessions_run_today))
            if not any_key.startswith(today_str):
                sessions_run_today = set()
                log("新交易日，重置会话记录")

        if is_trading_day():
            for h, m, session_type in schedule:
                session_key = f"{today_str}-{h:02d}{m:02d}-{session_type}"
                if session_key in sessions_run_today:
                    continue
                if now.hour == h and now.minute == m:
                    log(f"触发: {session_type} 会话 ({h:02d}:{m:02d})")
                    if session_type == "intraday":
                        run_intraday_check()
                    else:
                        run_daily_analysis()
                    sessions_run_today.add(session_key)

        # 整分钟对齐休眠
        sleep_sec = 60 - datetime.now().second
        time.sleep(max(1, sleep_sec))


if __name__ == "__main__":
    main()
