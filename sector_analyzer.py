"""
P1-B：行业板块热度检测
识别当前港股哪些板块在集体上涨，提升该板块个股的推荐权重
数据来源：腾讯行情实时数据 + 板块分类字典
"""
import requests
import re
import logging
from real_data import HEADERS

# 港股主要板块分类（股票代码 → 板块）
SECTOR_MAP = {
    # 互联网/科技
    "0700.HK": "科技互联网", "9988.HK": "科技互联网", "9618.HK": "科技互联网",
    "0772.HK": "科技互联网", "3896.HK": "科技互联网", "9678.HK": "科技互联网",
    "2432.HK": "科技互联网", "6088.HK": "科技互联网", "6166.HK": "科技互联网",
    "9626.HK": "科技互联网", "1810.HK": "科技互联网", "3690.HK": "科技互联网",
    # 新能源/汽车
    "1211.HK": "新能源汽车", "2015.HK": "新能源汽车", "0175.HK": "新能源汽车",
    "2865.HK": "新能源汽车", "1057.HK": "新能源汽车", "3750.HK": "新能源汽车",
    "9866.HK": "新能源汽车", "2238.HK": "新能源汽车",
    # 金融/银行
    "0005.HK": "金融银行", "0011.HK": "金融银行", "2318.HK": "金融银行",
    "1299.HK": "金融银行", "0388.HK": "金融银行", "3988.HK": "金融银行",
    "1398.HK": "金融银行", "0939.HK": "金融银行", "1288.HK": "金融银行",
    # 消费/零售
    "9999.HK": "消费零售", "6862.HK": "消费零售", "0291.HK": "消费零售",
    "1929.HK": "消费零售", "2020.HK": "消费零售", "9633.HK": "消费零售",
    # 能源/资源
    "0857.HK": "能源资源", "0883.HK": "能源资源", "1033.HK": "能源资源",
    "1138.HK": "能源资源", "2899.HK": "能源资源", "1208.HK": "能源资源",
    "0386.HK": "能源资源", "0002.HK": "能源资源",
    # 医疗/生物
    "1177.HK": "医疗健康", "2269.HK": "医疗健康", "9926.HK": "医疗健康",
    "2582.HK": "医疗健康", "2629.HK": "医疗健康", "6160.HK": "医疗健康",
    # 地产
    "0016.HK": "地产", "0012.HK": "地产", "0101.HK": "地产",
    "0017.HK": "地产", "0688.HK": "地产",
    # 工业/制造
    "1072.HK": "工业制造", "1133.HK": "工业制造", "0753.HK": "工业制造",
    "2233.HK": "工业制造", "6869.HK": "工业制造",
    # 黄金/贵金属
    "3939.HK": "黄金贵金属", "2899.HK": "黄金贵金属", "3330.HK": "黄金贵金属",
    # 公用事业
    "1635.HK": "公用事业", "0003.HK": "公用事业",
    # AI/人工智能（扩展）
    "2513.HK": "AI人工智能", "0020.HK": "AI人工智能",
    "9888.HK": "AI人工智能", "1024.HK": "AI人工智能",
    "2382.HK": "AI人工智能", "0241.HK": "AI人工智能", "0100.HK": "AI人工智能",  # MiniMax
    "3738.HK": "AI人工智能", "9698.HK": "AI人工智能",
    "2400.HK": "AI人工智能", "1316.HK": "AI人工智能",
    # AI芯片/半导体（扩展）
    "6082.HK": "AI芯片半导体", "9903.HK": "AI芯片半导体",
    "2533.HK": "AI芯片半导体", "1347.HK": "AI芯片半导体",
    "6185.HK": "AI芯片半导体",
    # 机器人（扩展）
    "6600.HK": "机器人", "2252.HK": "机器人",
    # 云计算/SaaS
    "0909.HK": "AI人工智能", "3888.HK": "AI人工智能",
}

# 自动板块识别：根据股票名称关键词推断板块（用于SECTOR_MAP外的股票）
SECTOR_KEYWORDS = {
    "AI人工智能":   ["AI", "人工智能", "智能", "大模型", "算法", "智谱", "商汤", "旷视",
                    "云从", "寒武纪", "第四范式", "科大讯飞", "百度", "MiniMax", "稀宇",
                    "SENSETIME", "数据智能", "深度学习"],
    "AI芯片半导体": ["芯片", "半导体", "晶圆", "光刻", "集成电路", "IC", "GPU", "ASIC",
                    "存储", "华虹", "中芯", "芯源", "芯达", "芯原", "SEMICONDUCTOR"],
    "机器人":       ["机器人", "自动化", "智能制造", "工业母机", "ROBOT"],
    "新能源汽车":   ["新能源", "电动", "锂电", "动力电池", "充电", "蔚来", "理想", "小鹏",
                    "比亚迪", "宁德", "电池", "光伏", "太阳能", "风电", "氢能",
                    "储能", "EV", "CATL"],
    "科技互联网":   ["互联网", "电商", "社交", "游戏", "直播", "短视频", "SaaS", "云计算",
                    "软件", "信息技术", "数码", "科技", "网络", "电子商务", "TECH",
                    "快手", "京东", "美团", "腾讯", "阿里", "网易", "哔哩", "小米"],
    "医疗健康":     ["医药", "生物", "制药", "医疗", "基因", "疫苗", "医美", "药业",
                    "健康", "诊断", "器械", "PHARMA", "BIO", "药明", "百济",
                    "石药", "中国生物", "康臣", "翰森"],
    "金融银行":     ["银行", "保险", "证券", "基金", "信托", "金融", "资管", "期货",
                    "BANK", "INSURANCE", "招商", "平安", "国寿", "太保",
                    "交银", "中银", "建行", "工行", "农行", "邮储"],
    "能源资源":     ["石油", "天然气", "煤炭", "矿业", "能源", "有色", "铜", "铝",
                    "锂", "镍", "铁矿", "ENERGY", "OIL", "GAS", "COAL",
                    "中海油", "中石油", "中石化", "紫金", "洛阳钼", "兖矿"],
    "地产":         ["地产", "物业", "房产", "置业", "房地产", "PROPERTY", "物管",
                    "碧桂园", "恒大", "万科", "龙湖", "融创", "华润置地"],
    "黄金贵金属":   ["黄金", "白银", "贵金属", "Gold", "GOLD", "金矿", "金业",
                    "灵宝", "赤峰", "招金", "中国黄金", "山东黄金", "紫金矿业"],
    "消费零售":     ["消费", "零售", "食品", "饮料", "餐饮", "啤酒", "白酒", "乳业",
                    "服装", "运动", "安踏", "李宁", "特步", "海底捞",
                    "蒙牛", "伊利", "百胜", "达利", "周黑鸭"],
    "工业制造":     ["制造", "工程", "建设", "机械", "钢铁", "水泥", "建材",
                    "铁建", "中交", "中冶", "中车", "航天", "航空", "船舶",
                    "中联重科", "三一", "海螺", "电气", "电力设备"],
    "公用事业":     ["电力", "水务", "燃气", "环保", "供水", "供电", "供热",
                    "华润电力", "华能", "大唐", "国电", "长江电力",
                    "UTILITY", "POWER", "WATER", "GAS"],
    "通信":         ["通信", "电信", "移动", "联通", "中国铁塔", "TELECOM",
                    "5G", "光纤", "光缆", "中兴"],
    "交通运输":     ["航运", "港口", "物流", "快递", "航空", "铁路", "公路",
                    "中远", "招商局", "东航", "国航", "南航", "顺丰",
                    "SHIPPING", "LOGISTICS", "COSCO"],
}

# 板块代表性股票（用于快速判断板块整体涨跌）
SECTOR_BENCHMARKS = {
    "科技互联网":  ["0700.HK", "9988.HK", "9618.HK"],
    "新能源汽车":  ["1211.HK", "2015.HK", "0175.HK"],
    "金融银行":    ["0005.HK", "2318.HK", "0388.HK"],
    "消费零售":    ["9999.HK", "6862.HK"],
    "能源资源":    ["0857.HK", "2899.HK", "1208.HK"],
    "医疗健康":    ["1177.HK", "2269.HK"],
    "工业制造":    ["1072.HK", "2233.HK"],
    "黄金贵金属":  ["3939.HK"],
    "公用事业":    ["1635.HK", "0002.HK"],
    "AI人工智能":    ["2513.HK", "0020.HK", "9888.HK"],
    "AI芯片半导体":  ["6082.HK", "9903.HK", "2533.HK"],
    "机器人":       ["6600.HK"],
}


def fetch_sector_performance() -> dict:
    """
    拉取各板块代表股的实时涨跌幅，计算板块平均涨幅
    返回: {板块名: {"avg_chg": float, "stocks": [...]}}
    """
    all_tickers = list({t for tickers in SECTOR_BENCHMARKS.values() for t in tickers})

    # 批量拉实时行情
    tc_codes = ",".join([
        "r_hk" + t.replace(".HK", "").zfill(5) for t in all_tickers
    ])
    try:
        r = requests.get(
            f"https://sqt.gtimg.cn/utf8/q={tc_codes}",
            headers=HEADERS, timeout=10
        )
        r.encoding = "utf-8"
    except Exception as e:
        logging.warning(f"[sector] 拉取板块实时行情失败: {e}")
        return {}

    price_map = {}
    for line in r.text.strip().split("\n"):
        m = re.match(r'v_r_(hk\d+)="([^"]+)"', line)
        if not m:
            continue
        f = m.group(2).split("~")
        if len(f) < 33:
            continue
        code = m.group(1)[2:]  # hk00700 → 00700
        ticker = f"{int(code)}.HK"
        try:
            price = float(f[3])
            prev  = float(f[4])
            chg_pct = ((price - prev) / prev * 100) if prev > 0 else 0
            price_map[ticker] = {"price": price, "chg_pct": round(chg_pct, 2), "name": f[1]}
        except Exception as e:
            logging.warning(f"[sector] 解析股票行情失败: {e}")

    # 计算各板块平均涨跌
    sector_perf = {}
    for sector, tickers in SECTOR_BENCHMARKS.items():
        changes = []
        stocks_info = []
        for t in tickers:
            if t in price_map:
                chg = price_map[t]["chg_pct"]
                changes.append(chg)
                stocks_info.append({
                    "ticker": t,
                    "name": price_map[t]["name"],
                    "chg_pct": chg,
                })
        if changes:
            avg = round(sum(changes) / len(changes), 2)
            sector_perf[sector] = {
                "avg_chg": avg,
                "stocks": stocks_info,
                "strength": _classify_strength(avg),
            }

    return sector_perf


def _classify_strength(avg_chg: float) -> str:
    if avg_chg >= 2.0:   return "强势 🔥"
    if avg_chg >= 0.5:   return "偏强 📈"
    if avg_chg >= -0.5:  return "平稳 ➡️"
    if avg_chg >= -2.0:  return "偏弱 📉"
    return "弱势 ❄️"


def get_hot_sectors(sector_perf: dict, top_n: int = 3) -> list[str]:
    """返回当日最强的 top_n 个板块名称"""
    if not sector_perf:
        return []
    sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1]["avg_chg"], reverse=True)
    return [s[0] for s in sorted_sectors[:top_n] if s[1]["avg_chg"] > 0]


def get_sector(ticker: str, name: str = "") -> str:
    """查询某只股票所属板块，支持自动识别"""
    # 1. 先查精确映射表
    if ticker in SECTOR_MAP:
        return SECTOR_MAP[ticker]
    # 2. 根据股票名称自动推断板块
    if name:
        for sector, keywords in SECTOR_KEYWORDS.items():
            for kw in keywords:
                if kw in name:
                    return sector
    return "其他"


def sector_score_boost(ticker: str, hot_sectors: list[str], name: str = "") -> int:
    """
    如果该股票属于热门板块，加 1 分
    """
    sector = get_sector(ticker, name)
    if sector in hot_sectors:
        return 1
    return 0


def get_sector_report(sector_perf: dict) -> str:
    """生成板块热度报告"""
    if not sector_perf:
        return ""

    sorted_s = sorted(sector_perf.items(), key=lambda x: x[1]["avg_chg"], reverse=True)
    lines = ["\n📊 今日板块热度"]
    for sector, info in sorted_s:
        avg = info["avg_chg"]
        strength = info["strength"]
        lines.append(f"  {sector:8} {avg:+.2f}%  {strength}")

    return "\n".join(lines)


if __name__ == "__main__":
    print("拉取板块数据中...")
    perf = fetch_sector_performance()
    print(get_sector_report(perf))
    hot = get_hot_sectors(perf)
    print(f"\n🔥 今日热门板块：{'、'.join(hot) if hot else '无明显强势板块'}")
