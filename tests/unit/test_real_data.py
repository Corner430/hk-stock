"""Tests for hkstock.data.real_data module (pure functions only)."""
from hkstock.data.real_data import ticker_to_tencent


class TestTickerToTencent:
    def test_normal_4digit(self):
        assert ticker_to_tencent("0700.HK") == "hk00700"

    def test_normal_5digit(self):
        assert ticker_to_tencent("09988.HK") == "hk09988"

    def test_short_code(self):
        assert ticker_to_tencent("0005.HK") == "hk00005"

    def test_single_digit(self):
        """Single digit should be zero-padded to 5."""
        assert ticker_to_tencent("3.HK") == "hk00003"

    def test_hsi_index(self):
        assert ticker_to_tencent("HSI.HI") == "hkHSI"

    def test_hsi_short(self):
        assert ticker_to_tencent("HSI") == "hkHSI"

    def test_lowercase_hk(self):
        assert ticker_to_tencent("0700.hk") == "hk00700"

    def test_five_digit_code(self):
        """Codes like 06088.HK should produce hk06088."""
        assert ticker_to_tencent("06088.HK") == "hk06088"
