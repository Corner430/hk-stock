"""Tests for hkstock.analysis.sector module."""
from hkstock.analysis.sector import (
    get_sector,
    get_hot_sectors,
    get_cold_sectors,
    sector_score_boost,
    get_sector_report,
    _classify_strength,
    SECTOR_MAP,
    SECTOR_KEYWORDS,
)


class TestGetSector:
    def test_exact_match(self):
        """Ticker in SECTOR_MAP should return its sector."""
        assert get_sector("0700.HK") == "科技互联网"
        assert get_sector("1211.HK") == "新能源汽车"
        assert get_sector("0005.HK") == "金融银行"

    def test_keyword_match(self):
        """Name keyword should trigger sector classification."""
        # Use tickers NOT in SECTOR_MAP so keyword matching is exercised
        assert get_sector("8888.HK", "网易-S") == "科技互联网"
        assert get_sector("7777.HK", "某某银行") == "金融银行"
        assert get_sector("6666.HK", "某某石油集团") == "能源资源"

    def test_unknown_returns_other(self):
        """Unknown ticker with no keyword match returns '其他'."""
        assert get_sector("9999.HK") == "消费零售"  # in SECTOR_MAP
        assert get_sector("0001.HK") == "其他"  # not in map, no name
        assert get_sector("0001.HK", "某某控股") == "其他"

    def test_empty_name(self):
        """Empty name should not crash."""
        assert get_sector("0001.HK", "") == "其他"


class TestGetHotSectors:
    def test_empty_input(self):
        assert get_hot_sectors({}) == []

    def test_returns_positive_only(self):
        perf = {
            "科技互联网": {"avg_chg": 2.5, "stocks": [], "strength": "强势"},
            "金融银行": {"avg_chg": -1.0, "stocks": [], "strength": "偏弱"},
            "新能源汽车": {"avg_chg": 0.8, "stocks": [], "strength": "偏强"},
        }
        hot = get_hot_sectors(perf, top_n=3)
        assert "科技互联网" in hot
        assert "新能源汽车" in hot
        assert "金融银行" not in hot

    def test_top_n_limit(self):
        perf = {
            "A": {"avg_chg": 3.0, "stocks": [], "strength": ""},
            "B": {"avg_chg": 2.0, "stocks": [], "strength": ""},
            "C": {"avg_chg": 1.0, "stocks": [], "strength": ""},
        }
        hot = get_hot_sectors(perf, top_n=2)
        assert len(hot) == 2
        assert hot[0] == "A"


class TestGetColdSectors:
    def test_empty_input(self):
        assert get_cold_sectors({}) == []

    def test_returns_negative_only(self):
        perf = {
            "科技互联网": {"avg_chg": 2.5, "stocks": [], "strength": ""},
            "地产": {"avg_chg": -3.0, "stocks": [], "strength": ""},
            "能源资源": {"avg_chg": -1.0, "stocks": [], "strength": ""},
        }
        cold = get_cold_sectors(perf, bottom_n=3)
        assert "地产" in cold
        assert "能源资源" in cold
        assert "科技互联网" not in cold

    def test_bottom_n_limit(self):
        perf = {
            "A": {"avg_chg": -5.0, "stocks": [], "strength": ""},
            "B": {"avg_chg": -3.0, "stocks": [], "strength": ""},
            "C": {"avg_chg": -1.0, "stocks": [], "strength": ""},
        }
        cold = get_cold_sectors(perf, bottom_n=1)
        assert len(cold) == 1
        assert cold[0] == "A"


class TestSectorScoreBoost:
    def test_hot_sector_basic(self):
        boost = sector_score_boost("0700.HK", ["科技互联网"])
        assert boost == 1  # no sector_perf, default +1

    def test_hot_sector_strong(self):
        perf = {"科技互联网": {"avg_chg": 3.5, "stocks": []}}
        boost = sector_score_boost("0700.HK", ["科技互联网"], sector_perf=perf)
        assert boost == 3

    def test_hot_sector_moderate(self):
        perf = {"科技互联网": {"avg_chg": 1.8, "stocks": []}}
        boost = sector_score_boost("0700.HK", ["科技互联网"], sector_perf=perf)
        assert boost == 2

    def test_cold_sector(self):
        perf = {"金融银行": {"avg_chg": -3.5, "stocks": []}}
        boost = sector_score_boost(
            "0005.HK", [], cold_sectors=["金融银行"], sector_perf=perf
        )
        assert boost == -2

    def test_neutral_sector(self):
        boost = sector_score_boost("0001.HK", ["科技互联网"])
        assert boost == 0


class TestClassifyStrength:
    def test_strong(self):
        assert "强势" in _classify_strength(2.5)

    def test_mild_up(self):
        assert "偏强" in _classify_strength(1.0)

    def test_flat(self):
        assert "平稳" in _classify_strength(0.0)

    def test_mild_down(self):
        assert "偏弱" in _classify_strength(-1.0)

    def test_weak(self):
        assert "弱势" in _classify_strength(-2.5)


class TestGetSectorReport:
    def test_empty(self):
        assert get_sector_report({}) == ""

    def test_non_empty(self):
        perf = {
            "科技互联网": {"avg_chg": 2.0, "stocks": [], "strength": "强势 🔥"},
        }
        report = get_sector_report(perf)
        assert "科技互联网" in report
        assert "板块热度" in report
