"""Tests for hkstock.analysis.indicators module."""
import pandas as pd
import numpy as np
import pytest
from hkstock.analysis.indicators import (
    calc_rsi, calc_macd, calc_bollinger, calc_adx, calc_atr, calc_momentum,
    resample_to_weekly,
)


@pytest.fixture
def price_series():
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    return pd.Series(prices, index=pd.date_range("2025-01-01", periods=100, freq="B"))


@pytest.fixture
def ohlcv_df(price_series):
    close = price_series
    high = close + np.random.rand(len(close)) * 2
    low = close - np.random.rand(len(close)) * 2
    volume = np.random.randint(100000, 1000000, size=len(close))
    return pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": high, "Low": low, "Close": close, "Volume": volume,
    }, index=close.index)


class TestCalcRSI:
    def test_returns_series(self, price_series):
        rsi = calc_rsi(price_series)
        assert isinstance(rsi, pd.Series)
        assert len(rsi) == len(price_series)

    def test_range_0_100(self, price_series):
        valid = calc_rsi(price_series).dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_all_up_high_rsi(self):
        up = pd.Series(range(1, 51), dtype=float)
        assert calc_rsi(up, period=14).iloc[-1] > 90


class TestCalcMACD:
    def test_returns_three_series(self, price_series):
        macd, signal, hist = calc_macd(price_series)
        assert isinstance(macd, pd.Series)
        assert isinstance(signal, pd.Series)
        assert isinstance(hist, pd.Series)

    def test_histogram_equals_diff(self, price_series):
        macd, signal, hist = calc_macd(price_series)
        pd.testing.assert_series_equal(hist, macd - signal, check_names=False)


class TestCalcBollinger:
    def test_upper_above_lower(self, price_series):
        upper, mid, lower = calc_bollinger(price_series)
        valid = upper.notna() & lower.notna()
        assert (upper[valid] >= lower[valid]).all()

    def test_mid_is_rolling_mean(self, price_series):
        upper, mid, lower = calc_bollinger(price_series, period=20)
        pd.testing.assert_series_equal(mid, price_series.rolling(20).mean(), check_names=False)


class TestCalcADX:
    def test_adx_non_negative(self, ohlcv_df):
        adx, _, _ = calc_adx(ohlcv_df["High"], ohlcv_df["Low"], ohlcv_df["Close"])
        assert (adx.dropna() >= 0).all()


class TestCalcATR:
    def test_atr_non_negative(self, ohlcv_df):
        atr = calc_atr(ohlcv_df["High"], ohlcv_df["Low"], ohlcv_df["Close"])
        assert (atr.dropna() >= 0).all()


class TestCalcMomentum:
    def test_absolute_momentum(self, price_series):
        mom = calc_momentum(price_series, period=20)
        assert isinstance(mom, pd.Series) and len(mom) == len(price_series)

    def test_relative_momentum(self, price_series):
        mom = calc_momentum(price_series, benchmark_close=price_series * 1.01, period=20)
        assert isinstance(mom, pd.Series)


class TestResampleToWeekly:
    def test_weekly_output(self, ohlcv_df):
        weekly = resample_to_weekly(ohlcv_df)
        assert len(weekly) < len(ohlcv_df)
        assert {"Open", "High", "Low", "Close", "Volume"}.issubset(weekly.columns)
