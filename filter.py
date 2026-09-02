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
    "明星同款", "娱乐圈", "爱豆", "亮相",
]

# 游戏新闻关键词（游戏资讯不属于要闻，热点中也以社会新闻为主）
GAME_KEYWORDS = [
    "上线", "公测", "内测", "首发", "登陆", "登录", "DLC", "资料片",
    "steam", "Steam", "Epic", "Switch", "PS5", "Xbox", "手游", "端游",
    "玩家", "副本", "赛季", "版本更新", "补丁", "新英雄", "新皮肤",
    "典藏版", "重制版", "复刻版", "打折", "优惠", "特惠", "喜加一",
    # 游戏硬件/电竞（数码软文常借游戏名义，标题或摘要命中即算）
    "游戏手机", "游戏本", "电竞本", "电竞", "开黑", "联机", "网游",
    "MOBA", "游戏党", "游戏性能", "帧率", "显卡",
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
    r"首次亮相|红毯亮相|当选.*团长|任.*团长",                   # 娱乐/文艺人事
    r"夺冠.*奥运|夺冠.*冠军|网球.*夺冠|篮球.*夺冠|足球.*夺冠",  # 体育赛事
    # 公司/股东增减持（纯资本运作，无大众公共价值）
    r"(?:获|遭|完成|拟|再获|实施).{0,10}(?:增持|减持)",          # 获XX大手笔增持
    r"(?:增持|减持).{0,10}(?:股份|股权|万股|亿股|港元|美元|涉资|%股权|大手笔)",  # 增持XX股份
    r"(?:增持|减持).{0,8}(?:涉资|套现|大手笔|连续)",             # 增持涉资X
    # 公司财年/财季财报（外国公司财报非宏观要闻，源头即拦截）
    r"财年.{0,14}(?:营收|净利|净亏|利润|亏损|财报)",             # 2027财年第二财季营收X
    r"(?:营收|净利|利润|亏损).{0,14}财年",                       # 营收X亿…2027财年
    r"(?:营收|收入|净利|利润|亏损|销售额).{0,20}亿(?:美元|欧元|港元|日元|英镑)",  # 营收470亿美元
    # 网络热梗式标题（无信息量，蹭热度文）
    r"承包[了]?.{0,10}(?:笑点|泪点|热搜|流量)",                  # 承包了全网笑点
    r"(?:万万没想到|破防了|绝绝子|YYDS|yyds|狠狠爱住|坐不住了|全网沸腾)",  # 网络热梗
    # 数码导购/评测软文（电商软文，不是新闻）
    r"(?:怎么选|如何选|哪款好|选购指南|购买指南|横评|实测对比|性价比对比|值得买|好不好用|可以选吗)",
    r"(?:游戏手机|游戏本|电竞本|游戏党|电竞屏)",                  # 游戏硬件导购
    r"^重新定义.{0,12}",                                        # 营销腔标题（多为软广）
    r"(?:高性价比|好用不贵|闭眼入|入手不亏|种草)",                # 导购话术
]

SEEN_KEEP_DAYS = 3   # 历史去重记录保留天数（缩短到3天，避免旧新闻长期占坑导致内容重复）


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
        # 股东增减持等个股资本运作、金融机构人事任命/债券发行（对大众无感）
        "增持", "减持", "任职资格", "获批担任", "获批任职",
        "金融债券", "中期票据", "超短期融资券", "可转债",
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


def is_sports_news(title):
    """是否属于体育赛事结果新闻（非体育经济/产业新闻）。"""
    sports_indicators = ["夺冠", "冠军", "亚军", "季军", "金牌", "银牌", "铜牌", "破纪录", "卫冕", "绝杀", "逆转"]
    sports_events = ["奥运会", "奥运", "冬奥会", "亚运会", "全运会", "世锦赛", "世界杯"]
    sports_types = ["网球", "篮球", "足球", "排球", "乒乓球", "羽毛球", "游泳", "田径", "体操"]
    
    has_indicator = any(kw in title for kw in sports_indicators)
    has_event = any(kw in title for kw in sports_events)
    has_type = any(kw in title for kw in sports_types)
    
    # 有赛事指标 + 有赛事名称/类型 = 体育新闻
    if has_indicator and (has_event or has_type):
        return True
    return False


def is_low_quality(title, summary=""):
    """综合判断是否为低质量新闻，应被过滤。

    标题与摘要一并检查：很多游戏/导购软文的特征词只出现在摘要里
    （如"游戏手机对比"的摘要列出《英雄联盟》《CS2》等游戏名），
    只看标题会漏掉。摘要只取前 150 字，避免长文里偶然出现的词误伤。
    """
    text = title
    if summary:
        text = title + " " + str(summary)[:150]
    return (
        is_gossip(text)
        or is_game_news(text)
        or is_investment_noise(text)
        or is_sports_news(text)
    )


# ============== 重要性评分：规则降级时按分数排序 ==============

# 高重要性：政治、外交、重大政策、灾难、重大社会事件
HIGH_IMPORTANCE_KEYWORDS = [
    # 中国政治
    "总书记", "国家主席", "国务院", "总理", "全国人大", "政协", "两会",
    "中央", "中共中央", "中办", "国办", "外交部", "国防部", "发改委",
    "央行", "证监会", "银保监", "工信部", "公安部", "教育部", "卫健委",
    "政策", "新规", "条例", "法案", "法律", "法规", "国务院常务会议",
    # 国际政治（权重最高）
    "总统", "首相", "总理", "国王", "主席", "联合国", "欧盟", "北约",
    "G7", "G20", "APEC", "金砖", "上合组织", "白宫", "克里姆林宫", "国会",
    "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "金正恩", "莫迪",
    "哈梅内伊", "内塔尼亚胡", "苏纳克", "岸田", "朔尔茨", "埃尔多安",
    "伊朗", "以色列", "巴勒斯坦", "黎巴嫩", "叙利亚", "伊拉克", "阿富汗",
    "也门", "胡塞", "哈马斯", "真主党", "塔利班",
    # 重大事件
    "地震", "台风", "洪水", "火灾", "爆炸", "事故", "遇难", "死亡",
    "疫情", "疫苗", "战争", "冲突", "空袭", "导弹", "制裁", "停火",
    "峰会", "会晤", "访问", "会谈", "签署", "协议", "条约", "宣言",
    "谈判", "撤军", "入侵", "占领", "轰炸", "击落", "击沉",
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

    # 旧闻降权：标题包含过去月份（非当前月）或明确旧年份，大幅降低分数
    current_month = datetime.now().month
    # 匹配 "X月" 或 "XX月" 格式
    month_match = re.search(r"(\d{1,2})月", title)
    if month_match:
        news_month = int(month_match.group(1))
        if news_month != current_month and 1 <= news_month <= 12:
            score -= 100  # 旧月份新闻大幅降权
    # 匹配旧年份（2020-2025），无论是否带"年"字（如"2025中法文化之春"也能命中）
    year_match = re.search(r"(?<!\d)202[0-5](?!\d)", title)
    if year_match:
        score -= 150  # 旧年份新闻几乎排除

    # 来源加分：权威媒体优先
    source = news_item.get("source", "")
    if "新浪" in source:
        score += 20
    if "早报" in source:
        score += 18  # 联合早报：国际新闻质量高
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

    # 关键词加分（国际政治人物/冲突地区权重最高）
    intl_politics = [
        "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "金正恩", "莫迪",
        "哈梅内伊", "内塔尼亚胡", "伊朗", "以色列", "巴勒斯坦", "黎巴嫩",
        "叙利亚", "伊拉克", "阿富汗", "也门", "胡塞", "哈马斯", "真主党",
        "战争", "冲突", "空袭", "导弹", "制裁", "停火", "谈判", "撤军",
        "入侵", "占领", "轰炸", "击落", "击沉",
    ]
    for kw in intl_politics:
        if kw in title:
            score += 80  # 国际政治/军事新闻最高优先级
            break

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

    # 财经市场/公司动态降权：早报要闻以政治/外交/军事/灾害/民生为主，
    # 央行加息、公司持股、裁员、负债等属于专业财经，规则降级时不该占满版面
    # （降权而非过滤：当天确实只有财经要闻时仍可入选）
    finance_noise = [
        "加息", "降息", "通胀", "通缩", "国债收益率", "日经", "道指", "纳指",
        "美股", "港股", "A股", "股价", "持股", "市值", "营收", "财报",
        "裁员", "裁减", "负债", "商社", "基金", "投资者", "招股",
    ]
    if any(kw in title for kw in finance_noise):
        score -= 40

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


MAX_NEWS_AGE_DAYS = 2   # 带时间戳的新闻最多保留 2 天内的（今天+昨天）


def _parse_item_datetime(n):
    """从新闻条目解析发布时间（ctime 支持时间戳或日期字符串），失败返回 None。"""
    raw = str(n.get("ctime", "") or "").strip()
    if not raw:
        return None
    # 纯数字按 Unix 时间戳处理
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(int(raw))
        except (ValueError, OSError, OverflowError):
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def filter_by_date(news):
    """过滤旧闻：优先按时间戳硬过滤，其次按标题/摘要中的日期文本过滤。

    - 条目带可解析的发布时间（ctime）：超过 MAX_NEWS_AGE_DAYS 天直接丢弃
      （可杜绝天行/聚合等接口返回的陈年缓存新闻）
    - 文本中出现旧年份（2020-2025，不论是否带"年"字）或明显过去月份：丢弃
    """
    now = datetime.now()
    current_month = now.month
    filtered = []
    for n in news:
        # 1. 时间戳硬过滤（最可靠）
        dt = _parse_item_datetime(n)
        if dt is not None and (now - dt).days >= MAX_NEWS_AGE_DAYS:
            continue
        text = n.get("title", "") + " " + n.get("summary", "")
        # 2. 排除旧年份（2020-2025，带不带"年"字都算，如"2025中法文化之春"）
        if re.search(r"(?<!\d)202[0-5](?!\d)", text):
            continue
        # 排除过去月份（非当前月8月，且是6月及更早的）
        # 匹配单个月份："4月"、"5月"等
        month_match = re.search(r"(\d{1,2})月", text)
        if month_match:
            news_month = int(month_match.group(1))
            if news_month != current_month and news_month <= 6:
                continue
        # 匹配月份范围："4至7月"、"4-7月"、"4~7月"等
        range_match = re.search(r"(\d{1,2})[\-~至](\d{1,2})月", text)
        if range_match:
            start_month = int(range_match.group(1))
            end_month = int(range_match.group(2))
            # 如果范围完全在当前月之前（如4-7月，当前是8月）
            if end_month < current_month:
                continue
        filtered.append(n)
    return filtered


def filter_quality(news):
    """过滤低质量新闻（花边/游戏/投资噪音），返回过滤后的列表。"""
    filtered = []
    for n in news:
        if is_low_quality(n["title"], n.get("summary", "")):
            continue
        filtered.append(n)
    return filtered
