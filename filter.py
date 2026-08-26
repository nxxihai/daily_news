"""过滤层：花边/游戏/个股研报黑名单 + 标题去重（跨天）+ 重要性评分。

设计要点：
- seen 记录「已成功推送」的标题哈希，而不是「已采集」的，
  这样推送失败重跑时不会把新闻误判为已推送。
- 保存时只保留最近 keep_days 天的记录，防止历史无限增长。
- 重要性评分用于规则降级排序：政治/重大事件 > 经济民生 > 科技社会 > 体育游戏
"""

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# ============== 黑名单：命中即过滤，不进入候选池 ==============

# 花边 / 娱乐 / 低信息量关键词
GOSSIP_KEYWORDS = [
    "明星", "绯闻", "恋情", "分手", "出轨", "八卦", "吃瓜", "网红",
    "综艺", "选秀", "剧组", "票房", "番剧", "演唱会", "应援", "粉丝",
    "塌房", "官宣", "路透", "生图", "剧透", "爆料", "开撕", "互撕",
    "炒作", "蹭热度", "微博之夜", "跨年晚会", "红毯", "带货", "站姐",
    "婚变", "情变", "小三", "傍大款", "整容", "离婚协议", "被曝",
    # 电影/影评/娱乐节目
    "影迷评", "观影团", "首映礼", "首映抢票", "免费抢票", "笑点密集",
    "青春特辑", "影迷", "影评", "豆瓣", "烂片", "好片",
    "明星同款", "娱乐圈", "爱豆",
]

# 游戏新闻关键词（游戏资讯不属于要闻，热点中也以社会新闻为主）
GAME_KEYWORDS = [
    "上线", "公测", "内测", "首发", "登陆", "登录", "DLC", "资料片",
    "steam", "Steam", "Epic", "Switch", "PS5", "Xbox", "手游", "端游",
    "玩家", "副本", "赛季", "版本更新", "补丁", "新英雄", "新皮肤",
    "典藏版", "重制版", "复刻版", "打折", "优惠", "特惠", "喜加一",
]

# 个股/期货/ETF/研报/彩票/博彩类关键词（纯投资/博彩信息不是大众新闻）
INVESTMENT_KEYWORDS = [
    "早报", "午报", "晚报", "日报", "周报", "月报", "盘前", "盘后",
    "个股", "涨停", "跌停", "收涨", "收跌", "涨超", "跌超", "反弹超",
    "沪指", "深指", "创业板指", "科创板", "北交所", "北向资金", "南向资金",
    "期货", "原油期货", "黄金期货", "钢材", "橡胶", "能化", "黑色系",
    "ETF", "基金净值", "重仓股", "研报", "评级", "目标价", "增持", "减持",
    "招股", "IPO", "打新", "中签", "解禁", "分红", "送转",
    "半年报", "三季报", "年报", "财报季", "营收同比", "净利润同比",
    "港股通", "互联互通", "融资融券", "两融", "配资",
    # 彩票/博彩
    "双色球", "大乐透", "福彩", "体彩", "中奖", "彩票",
    "胜负彩", "任九", "足彩", "赔率", "盘口", "欧亚赔率", "竞彩",
    "投注", "倍投", "复式", "单式", "开奖",
    # 商品/加密货币价格（非宏观经济）
    "现货白银", "现货黄金", "现货铜", "纯苯", "花生", "豆粕", "菜粕",
    "比特币", "以太坊", "加密货币", "虚拟货币", "狗狗币",
    "概念股", "板块走强", "板块走弱", "涨超", "跌超", "涨幅", "跌幅",
    "日内跌幅", "日内涨幅", "现报", "美元/盎司", "美元/桶",
    # 财经快讯
    "快讯：", "港股", "美股", "A股收评", "收评", "午评",
]

# 低价值内容模式（正则匹配）
LOW_VALUE_PATTERNS = [
    r"^\d+月\d+日(?:黑色系|能化|农产品|金属|贵金属)早报",   # 期货早报
    r"^.{0,6}(?:反弹|涨超|跌超|大涨|大跌|飙升|暴跌)\d+%",      # 个股涨跌幅
    r"(?:上线|公测|发售|登陆|登录)(?:PC|Steam|Epic|Switch|PS|Xbox|手游|端游)",  # 游戏上线
    r"《[^》]{2,15}》(?:新|首推|将推|上线|公测|发售|打折|优惠|模式|DLC)",  # 游戏相关
    r"精选双色球|大乐透\d+期|胜负彩\d+期|足彩\d+期",           # 彩票
    r"赔率对照|盘口存差异|相同盘口|欧洲四大机构",               # 博彩
    r"^快讯：",                                               # 财经快讯（多为商品价格/个股）
    r"现货(?:白银|黄金|铜|原油|天然气)",                        # 商品价格
    r"比特币.*(?:突破|跌破|升破|涨|跌|万美元|美元)",            # 加密货币价格
    r"概念股(?:走强|走弱|涨停|跌停|涨超|跌超)",                 # 概念股
    r"投票调查[：:]?",                                         # 投票调查
    r"微博观影团|影迷评|笑点密集",                             # 影评
    r"发布(?:20\d{2}|最新).*?(?:中期|年度|季度)业绩",           # 公司业绩公告
    r"营业收入\d+[.亿万]+.*?同比(?:增长|下降|增|降)",            # 公司营收数据
]

SEEN_KEEP_DAYS = 30  # 历史去重记录保留天数


def normalize_title(title):
    """去掉标点、空白后归一化，避免同一新闻因标点差异判重失败。"""
    return re.sub(r"[\W_]+", "", title).lower()


def title_hash(title):
    """生成标题哈希，用于跨天去重。"""
    return hashlib.md5(normalize_title(title).encode("utf-8")).hexdigest()


def is_gossip(title):
    """是否属于花边/娱乐新闻。"""
    return any(kw in title for kw in GOSSIP_KEYWORDS)


def is_game_news(title):
    """是否属于游戏新闻。"""
    title_lower = title.lower()
    # 先检查游戏关键词组合（避免"上线"等词误杀非游戏内容）
    game_indicators = 0
    for kw in GAME_KEYWORDS:
        if kw.lower() in title_lower:
            game_indicators += 1
    # 有两个以上游戏关键词，或者有书名号+游戏关键词，判定为游戏新闻
    if game_indicators >= 2:
        return True
    # 书名号内容 + 游戏相关动词
    if re.search(r"《[^》]+》", title):
        game_verbs = ["上线", "公测", "发售", "打折", "新模式", "新皮肤", "新英雄", "DLC", "典藏版", "重制版"]
        if any(v in title for v in game_verbs):
            return True
    # 明确的低价值模式匹配
    for pat in LOW_VALUE_PATTERNS:
        if re.search(pat, title):
            return True
    return False


def is_investment_noise(title):
    """是否属于个股/期货/研报/彩票/博彩等投资噪音（非大众新闻）。"""
    # 明确的低价值模式（正则直接命中即过滤）
    for pat in LOW_VALUE_PATTERNS:
        if re.search(pat, title):
            return True

    # 彩票/博彩/商品价格/加密货币类：命中1个关键词即过滤
    instant_filter = [
        "双色球", "大乐透", "福彩", "体彩", "彩票", "胜负彩", "足彩",
        "赔率", "盘口", "竞彩", "投注", "开奖",
        "现货白银", "现货黄金", "纯苯", "花生", "豆粕",
        "比特币", "以太坊", "加密货币", "虚拟货币",
        "快讯：", "概念股", "板块走强", "板块走弱",
        "日内跌幅", "日内涨幅", "美元/盎司", "美元/桶",
        "影迷评", "观影团", "笑点密集", "投票调查",
    ]
    for kw in instant_filter:
        if kw in title:
            return True

    # 一般投资类：命中2个以上关键词才过滤（避免误伤）
    general_invest = [
        "早报", "午报", "晚报", "日报", "盘前", "盘后",
        "个股", "涨停", "跌停", "收涨", "收跌", "涨超", "跌超",
        "沪指", "创业板指", "北向资金", "南向资金",
        "期货", "ETF", "研报", "评级", "目标价", "增持", "减持",
        "招股", "IPO", "打新", "中签", "解禁", "分红",
        "半年报", "三季报", "年报", "净利润同比", "营收同比",
        "港股通", "融资融券", "两融", "配资",
        "钢材", "橡胶", "能化", "黑色系",
    ]
    hit_count = sum(1 for kw in general_invest if kw in title)
    if hit_count >= 2:
        return True

    # 公司财报/涨跌幅模式（单条公司股价变动不是要闻）
    if re.search(r"^.{0,10}(?:反弹|涨超|跌超|大涨|大跌|飙升|暴跌|高开|低开)\d", title):
        return True
    if "经调整净利润" in title:
        return True

    return False


def is_low_quality(title):
    """综合判断是否为低质量新闻，应被过滤。"""
    return is_gossip(title) or is_game_news(title) or is_investment_noise(title)


# ============== 重要性评分：规则降级时按分数排序 ==============

# 高重要性：政治、外交、重大政策、灾难、重大社会事件
HIGH_IMPORTANCE_KEYWORDS = [
    # 中国政治
    "总书记", "国家主席", "国务院", "总理", "全国人大", "政协", "两会",
    "中央", "中共中央", "中办", "国办", "外交部", "国防部", "发改委",
    "央行", "证监会", "银保监", "工信部", "公安部", "教育部", "卫健委",
    "政策", "新规", "条例", "法案", "法律", "法规", "国务院常务会议",
    # 国际政治
    "总统", "首相", "总理", "国王", "主席", "联合国", "欧盟", "北约",
    "G7", "G20", "APEC", "金砖", "上合组织", "白宫", "克里姆林宫", "国会",
    "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "金正恩", "莫迪",
    # 重大事件
    "地震", "台风", "洪水", "火灾", "爆炸", "事故", "遇难", "死亡",
    "疫情", "疫苗", "战争", "冲突", "空袭", "导弹", "制裁", "停火",
    "峰会", "会晤", "访问", "会谈", "签署", "协议", "条约", "宣言",
]

# 中重要性：经济民生、科技突破、社会热点
MEDIUM_IMPORTANCE_KEYWORDS = [
    # 经济民生
    "GDP", "CPI", "PPI", "PMI", "降息", "降准", "加息", "通胀", "通缩",
    "就业", "失业", "社保", "医保", "养老金", "退休", "生育", "教育",
    "高考", "考研", "房价", "楼市", "调控", "油价", "电价", "水价",
    "高铁", "地铁", "机场", "高速", "桥梁", "隧道",
    # 科技
    "芯片", "半导体", "人工智能", "AI", "大模型", "量子", "航天",
    "卫星", "火箭", "探月", "火星", "空间站", "华为", "小米", "比亚迪",
    "宁德时代", "新能源", "光伏", "储能", "电动车", "自动驾驶",
    # 社会
    "警方", "通报", "调查", "抓获", "破获", "宣判", "判决", "案件",
    "救援", "搜救", "失联", "失踪", "被困", "获救",
]

# 低重要性：体育、娱乐、日常财经
LOW_IMPORTANCE_KEYWORDS = [
    "中超", "CBA", "NBA", "英超", "西甲", "意甲", "德甲", "法甲", "欧冠",
    "世界杯", "奥运会", "亚运会", "全运会", "世锦赛", "冠军", "亚军", "季军",
    "国足", "女排", "乒协", "足协", "篮协", "教练", "球员", "转会",
]


def importance_score(news_item):
    """给新闻打重要性分数（规则降级时排序用），分数越高越重要。"""
    title = news_item["title"]
    score = 0
    cat = news_item.get("category", "")

    # 来源加分：权威媒体优先
    source = news_item.get("source", "")
    if "新浪" in source:
        score += 20
    if "天行" in source:
        score += 15
    if "百度" in source:
        score += 10  # 百度热搜是热点，但非传统要闻
    if "聚合" in source:
        score += 5

    # 分类基础分
    if cat in ("国际要闻", "国际"):
        score += 30
    elif cat in ("国内", "国内要闻"):
        score += 30
    elif cat in ("军事",):
        score += 25
    elif cat in ("社会",):
        score += 20  # 社会新闻在"热点"中优先
    elif cat in ("科技",):
        score += 15
    elif cat in ("财经", "财经要闻"):
        score += 10  # 财经要闻降权（期货/个股已被过滤，剩下的宏观财经保留）
    elif cat in ("热点",):
        score += 18
    else:
        score += 5

    # 关键词加分
    for kw in HIGH_IMPORTANCE_KEYWORDS:
        if kw in title:
            score += 50
            break  # 命中高重要性关键词即大幅加分

    for kw in MEDIUM_IMPORTANCE_KEYWORDS:
        if kw in title:
            score += 25
            break

    for kw in LOW_IMPORTANCE_KEYWORDS:
        if kw in title:
            score -= 15
            break

    # 有摘要的新闻优先（信息更完整）
    if news_item.get("summary"):
        score += 5

    return score


# ============== 去重相关 ==============

def load_seen(path):
    """读取历史去重记录 {标题哈希: 日期}。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(path, seen):
    """保存历史去重记录，只保留最近 SEEN_KEEP_DAYS 天。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now() - timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y-%m-%d")
    cleaned = {h: d for h, d in seen.items() if d >= cutoff}
    path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def mark_seen(seen, titles, today=None):
    """把已推送成功的标题记入去重记录，返回更新后的字典。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    seen = dict(seen)
    for t in titles:
        seen[title_hash(t)] = today
    return seen


def filter_fresh(news, seen):
    """过滤掉历史中已推送过的新闻，返回新新闻列表。"""
    return [n for n in news if title_hash(n["title"]) not in seen]


def filter_quality(news):
    """过滤低质量新闻（花边/游戏/投资噪音），返回过滤后的列表。"""
    filtered = []
    for n in news:
        if is_low_quality(n["title"]):
            continue
        filtered.append(n)
    return filtered
