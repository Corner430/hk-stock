"""
技术指标计算模块 - 统一所有技术指标函数
使用 Wilder 平滑方法（与 TradingView / 通达信一致）
"""
import pandas as pd
import numpy as np


def calc_rsi(series, period=14):
    """
    计算 RSI 指标（Wilder 平滑法）
    与 TradingView 和通达信的计算方式一致
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    # Wilder 平滑 = EMA with alpha=1/period
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_macd(series, fast=12, slow=26, signal=9):
    """计算 MACD 指标"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def calc_bollinger(series, period=20, std=2):
    """计算布林带"""
    ma = series.rolling(window=period).mean()
    std_dev = series.rolling(window=period).std()
    upper = ma + std * std_dev
    lower = ma - std * std_dev
    return upper, ma, lower


def calc_adx(high, low, close, period=14):
    """
    计算 ADX（平均趋向指标）- Wilder 平滑法
    衡量趋势强度，与主流工具一致
    """
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    # Wilder 平滑（alpha=1/period）
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calc_atr(high, low, close, period=14):
    """
    计算 ATR（平均真实范围）- Wilder 平滑法
    用于波动率自适应仓位管理和止损距离计算
    """
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return atr


def calc_momentum(close, benchmark_close=None, period=20):
    """
    计算动量因子（相对强度）
    如果提供 benchmark_close（如恒生指数），则计算相对动量；
    否则计算绝对动量（period日涨幅）。

    返回: 动量百分比序列
    """
    if benchmark_close is not None:
        # 相对动量 = 个股涨幅 - 基准涨幅
        stock_ret = close.pct_change(period) * 100
        bench_ret = benchmark_close.pct_change(period) * 100
        return stock_ret - bench_ret
    else:
        # 绝对动量
        return close.pct_change(period) * 100


def resample_to_weekly(df):
    """
    将日线数据重采样为周线数据。
    输入 DataFrame 需要包含: Open, High, Low, Close, Volume 列，
    且 index 为 DatetimeIndex。

    返回: 周线 DataFrame
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)

    weekly = df.resample("W").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return weekly
