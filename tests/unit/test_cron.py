"""Tests for hkstock.app.cron module (pure functions only)."""
from datetime import datetime
from unittest.mock import patch
from hkstock.app.cron import is_trading_day


class TestIsTradingDay:
    def test_weekday_non_holiday(self):
        """A normal weekday should be a trading day."""
        # 2025-03-24 is a Monday
        with patch("hkstock.app.cron.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 3, 24, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_trading_day() is True

    def test_saturday(self):
        """Saturday should not be a trading day."""
        # 2025-03-22 is a Saturday
        with patch("hkstock.app.cron.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 3, 22, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_trading_day() is False

    def test_sunday(self):
        """Sunday should not be a trading day."""
        # 2025-03-23 is a Sunday
        with patch("hkstock.app.cron.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 3, 23, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_trading_day() is False

    def test_holiday(self):
        """A weekday that is in HK_HOLIDAYS should not be a trading day."""
        with patch("hkstock.app.cron.datetime") as mock_dt:
            # Use a date that's in HK_HOLIDAYS - pick first one from config
            from hkstock.core.config import HK_HOLIDAYS
            if HK_HOLIDAYS:
                holiday = sorted(HK_HOLIDAYS)[0]
                year, month, day = map(int, holiday.split("-"))
                fake_dt = datetime(year, month, day, 10, 0)
                # Only test if it's a weekday (holidays can fall on weekends)
                if fake_dt.weekday() < 5:
                    mock_dt.now.return_value = fake_dt
                    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                    assert is_trading_day() is False
