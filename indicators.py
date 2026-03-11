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
