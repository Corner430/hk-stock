"""
Flask Web 看板服务（含净值曲线图表）
"""
from flask import Flask, jsonify, request
import json, os, subprocess, sys, threading, time as _time

app = Flask(__name__)

# ── HTTP Basic Auth（在 .env 中配置 DASHBOARD_USER / DASHBOARD_PASS 开启）──
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")

@app.before_request
def check_auth():
    if not DASHBOARD_USER:
        return  # 未配置则不启用认证
    auth = request.authorization
    if not auth or auth.username != DASHBOARD_USER or auth.password != DASHBOARD_PASS:
        return ("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="HK-Stock Dashboard"'})

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

@app.route("/")
def index():
    template_path = os.path.join(TEMPLATE_DIR, "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.route("/api/data")
def api_data():
    f = os.path.join(os.path.dirname(__file__), "data", "latest.json")
    if not os.path.exists(f):
        return jsonify({"error": "no data", "stocks": [], "summary": {}}), 200
    with open(f, encoding="utf-8") as fp:
        return jsonify(json.load(fp))

@app.route("/api/portfolio")
def api_portfolio():
    f = os.path.join(os.path.dirname(__file__), "data", "portfolio.json")
    if not os.path.exists(f):
        return jsonify({"error": "no portfolio"}), 200
    with open(f, encoding="utf-8") as fp:
        return jsonify(json.load(fp))

_refresh_lock = threading.Lock()
_refresh_status = {"status": "idle", "started_at": None, "finished_at": None}

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 429
    _refresh_status["status"] = "running"
    _refresh_status["started_at"] = _time.time()
    _refresh_status["finished_at"] = None
    def run():
        try:
            script = os.path.join(os.path.dirname(__file__), "daily_report.py")
            subprocess.run([sys.executable, script], cwd=os.path.dirname(__file__))
        finally:
            _refresh_status["status"] = "idle"
            _refresh_status["finished_at"] = _time.time()
            _refresh_lock.release()
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/refresh/status")
def api_refresh_status():
    status = _refresh_status["status"]
    started = _refresh_status["started_at"]
    finished = _refresh_status["finished_at"]
    result = {"status": status}
    if started:
        result["started_at"] = started
        if status == "running":
            result["elapsed_seconds"] = round(_time.time() - started, 1)
    if finished:
        result["finished_at"] = finished
    latest = os.path.join(os.path.dirname(__file__), "data", "latest.json")
    if os.path.exists(latest):
        result["data_updated_at"] = os.path.getmtime(latest)
    return jsonify(result)

@app.route("/api/intraday-check", methods=["POST"])
def api_intraday_check():
    """盘中持仓检查（止损/止盈），供 hkstock_cron 定时调用"""
    try:
        from auto_trader import run_intraday_check
        logs = run_intraday_check()
        return jsonify({"status": "ok", "logs": logs})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ── 数据库查询接口 ──
@app.route("/api/db/runs")
def api_db_runs():
    """所有回测记录"""
    try:
        from database import get_all_runs
        return jsonify(get_all_runs())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/db/trades")
def api_db_trades():
    """交易记录，支持 ?run_id=xxx&ticker=xxx&limit=50"""
    try:
        from database import get_trade_history
        run_id = request.args.get("run_id")
        ticker = request.args.get("ticker")
        limit = int(request.args.get("limit", 50))
        return jsonify(get_trade_history(run_id, ticker, limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/db/snapshots")
def api_db_snapshots():
    """某次回测净值曲线，?run_id=xxx"""
    try:
        from database import get_snapshots
        run_id = request.args.get("run_id", "v2_20260225_20260305")
        return jsonify(get_snapshots(run_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/db/stock")
def api_db_stock():
    """某只股票历史评分，?ticker=2318.HK&days=30"""
    try:
        from database import get_stock_history
        ticker = request.args.get("ticker", "0700.HK")
        days = int(request.args.get("days", 30))
        return jsonify(get_stock_history(ticker, days))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/db/stats")
def api_db_stats():
    """策略统计：胜率、平均盈亏"""
    try:
        from database import get_stats_summary
        return jsonify(get_stats_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# /api/db/query 已移除（存在SQL注入风险）
# 如需查询，请使用预定义的 /api/db/trades, /api/db/stats 等接口

if __name__ == "__main__":
    print("🚀 启动港股分析看板...")
    print("📊 访问 http://localhost:8888 查看看板")
    app.run(host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"), port=int(os.environ.get("DASHBOARD_PORT", 8888)), debug=False)
