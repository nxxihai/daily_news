"""AI 提炼层：用 LLM 筛选最有价值的新闻并重写为短摘要。

核心改进：
- 支持多个 LLM Provider 按顺序尝试，第一个成功且格式达标即启用
- 对 429 限流错误支持指数退避重试；格式不达标时用强提示重试 1 次
- 全部 LLM 失败时，退回改进版规则筛选（带重要性评分排序）
- 输出严格校验：三个分类齐全、每条条数达标、中文占比合格、标题来自候选池（防编造）
- 分类标准：国际/国内以政治/外交/经济/民生要闻为主，热点以社会新闻/热议话题为主
"""

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
BJT = timezone(timedelta(hours=8))  # 北京时间

import requests

from config import (
    FALLBACK_EXTRA_PER_SECTION,
    LLM_PROVIDERS,
    NEWS_PER_LINE,
    NEWS_PER_SECTION,
    RETRY_BASE_DELAY,
)
from filter import importance_score, normalize_title

# 悬挂词：摘要若以这些词+句号结尾，说明是半句，应判定为截断
HANGING_WORDS = [
    "甚至", "但是", "然而", "因为", "所以", "如果", "尽管",
    "虽然", "不过", "而且", "并且", "由于", "随着", "通过",
    "根据", "对于", "关于", "为了", "除了", "经过",
    "构", "打", "将", "对", "向", "为", "从", "把", "被",
    "股", "元", "亿", "万", "人", "中", "上", "下", "前", "后",
    "可", "能", "会", "要", "需", "得", "让", "使", "令",
    "模", "式", "度", "法", "策", "施", "行", "动", "果",
    "北", "南", "东", "西",
    "有", "彼", "此", "其", "该", "各", "诸",
    "出", "来", "去", "起", "到", "及", "与", "和", "或", "等",
    "也", "还", "仍", "已", "将", "正", "在", "于", "之", "而",
]

SECTIONS = ["国际", "国内", "热点"]

# 最近一次 summarize() 实际走的通道：'llm'（AI 提炼）或 'rule'（规则精简版）。
# 供 run_daily 在规则兜底时标注，避免"质量下降"被误当成程序坏了。
LAST_ENGINE = ""


def engine_used():
    """最近一次 summarize() 用的是哪条通道：'llm' 或 'rule'。"""
    return LAST_ENGINE

# 采集源分类 → 早报三栏的映射（规则版用）
# 注意：社会新闻归入"热点"，财经/科技如果是要闻也可入国内但优先级低于政治社会
CATEGORY_MAP = {
    "国际": "国际", "国际要闻": "国际", "军事": "国际", "world": "国际",
    "国内": "国内", "国内要闻": "国内", "时政": "国内",
    "社会": "热点",           # 社会新闻优先归入热点
    "热点": "热点",
    "财经": "国内", "财经要闻": "国内",  # 宏观财经入国内（个股/期货已被filter过滤）
    "科技": "国内",           # 科技入国内
    "体育": "国内", "sports": "国内",
}

# 标题关键词 → 分类映射（规则版兜底用，避免源分类错误）
# 注意：不要把外国科技公司名（苹果/特斯拉/英伟达等）列入国际——那是财经/科技不是国际要闻
TITLE_KEYWORDS = {
    "国际": [
        # 中国元首外交（属于国际新闻）
        "习近平同", "习近平会见", "习近平与", "习近平致电", "习近平向",
        "李强同", "李强会见", "王毅同", "王毅会见", "外交部发言人",
        "外长", "国事访问", "正式访问", "通电话",
        # 外国政府/政治人物
        "美国", "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "金正恩",
        "欧盟", "北约", "联合国", "制裁", "贸易战", "关税",
        "美联储", "美元", "欧元", "英镑", "日元",
        "外交", "大使", "领事", "条约", "协议", "峰会", "会晤", "访问",
        "冲突", "战争", "军事", "空袭", "导弹", "核武", "恐怖袭击",
        "人质", "难民", "移民", "政变",
        "伊朗", "以色列", "俄罗斯", "乌克兰", "朝鲜", "韩国", "日本",
        "印度", "越南", "泰国", "新加坡", "澳大利亚",
        "英国", "法国", "德国", "意大利", "西班牙", "加拿大", "墨西哥",
        "巴西", "阿根廷", "南非", "埃及", "土耳其", "沙特", "阿联酋",
        "叙利亚", "伊拉克", "阿富汗", "巴基斯坦", "巴勒斯坦", "黎巴嫩",
        "约旦", "哈萨克斯坦", "荷兰", "瑞士", "瑞典",
        # 补充常见国家/地区：国家名不在表里时会掉进热点栏（如"巴拿马地震"）
        "巴拿马", "智利", "秘鲁", "哥伦比亚", "委内瑞拉", "玻利维亚", "厄瓜多尔",
        "古巴", "海地", "乌拉圭", "巴拉圭", "多米尼加",
        "尼日利亚", "肯尼亚", "埃塞俄比亚", "索马里", "苏丹", "加纳", "坦桑尼亚",
        "乌干达", "津巴布韦", "摩洛哥", "阿尔及利亚", "突尼斯", "利比亚",
        "希腊", "波兰", "匈牙利", "奥地利", "比利时", "葡萄牙", "挪威", "丹麦",
        "芬兰", "爱尔兰", "捷克", "罗马尼亚", "塞尔维亚", "克罗地亚", "保加利亚",
        "冰岛", "新西兰", "菲律宾", "马来西亚", "印度尼西亚", "孟加拉", "斯里兰卡",
        "缅甸", "蒙古", "乌兹别克斯坦", "亚美尼亚", "阿塞拜疆", "格鲁吉亚",
        "卡塔尔", "科威特", "阿曼", "巴林", "尼泊尔", "柬埔寨", "老挝",
        # 外国议员（中国是人大代表/政协委员，标题里的"议员"基本是外国政界）
        "议员",
        "外报", "外媒", "国际", "全球", "海外", "跨境", "中东",
        "G7", "G20", "APEC", "金砖", "上合", "东盟", "白宫", "克里姆林宫",
        "五角大楼", "国会", "参议院", "众议院", "首相", "总统", "总理",
        # 外国央行/国际经济政策
        "澳央行", "欧央行", "日本央行", "英国央行",
        # 国际知名人物（不应归国内）
        "比尔·盖茨", "盖茨", "马斯克", "巴菲特", "贝索斯", "扎克伯格",
        "诺贝尔", "奥斯卡", "格莱美",
        # 简写/组合词（标题常用简称，不含全称时也能识别）
        "中美", "中俄", "中日", "中韩", "外交部", "俄军", "乌军",
        "俄乌", "俄方", "乌方", "俄导弹", "北约",
    ],
    "国内": [
        "中国", "北京", "上海", "深圳", "广州", "杭州", "成都", "武汉",
        "国务院", "发改委", "央行", "证监会", "银保监", "工信部",
        "全国人大", "政协", "两会", "政府工作报告",
        "A股", "港股", "沪深", "上证指数", "人民币",
        "楼市", "房价", "房地产", "住房", "贷款", "房贷", "基建", "高铁", "地铁",
        "社保", "医保", "养老金", "就业", "生育", "教育", "高考",
        "疫情", "卫健委", "疾控",
        "乡村振兴", "碳中和", "新能源", "光伏",
        "芯片", "半导体", "人工智能", "航天",
        "华为", "小米", "比亚迪", "宁德时代", "腾讯", "阿里", "字节", "拼多多", "水滴",
        # 法规政策
        "法规", "条例", "新规", "政策", "办法", "意见",
    ],
    "热点": [
        "警方", "通报", "抓获", "破获", "案件", "事故", "遇难", "受伤",
        "火灾", "爆炸", "车祸", "坠楼", "溺水", "失踪", "失联",
        "救援", "搜救", "被困", "获救", "地震", "台风", "暴雨", "洪水",
        "热搜", "热议", "网友", "视频", "刷屏", "回应", "致歉", "道歉",
        "食品", "药品", "假货", "诈骗", "传销", "盗窃", "抢劫",
        "城管", "拆迁", "物业", "业主", "维权", "举报", "处罚",
        "幼儿园", "学校", "医院", "医生", "护士", "患者",
        "违规", "违法", "查处", "整治", "整改", "暗访", "卧底",
        "公交车", "车祸", "撞", "翻车",
        "教师", "合影", "耻辱", "争议", "引发",
        # 社会民生
        "月饼", "小蛇", "葡萄", "购物", "意外",
        "法拍", "别墅", "毛坯", "贷款",
        "一日游", "乡村", "孩子", "作业", "挑战赛",
        "蛋糕店", "店主", "回应",
        # 猎奇/社会事件（防止只占"国家名"一条国际词的奇闻混入国际栏）
        "摔角", "裁判", "婚礼", "新郎", "新娘", "可乐", "雪碧",
        "病房", "霉菌", "救护车", "门诊", "建卡", "老人",
    ],
}


# 明确的外国/国际实体：用于"国际 vs 热点 同分"时判断该条属于国际要闻
_FOREIGN_ANCHORS = [
    "美国", "俄罗斯", "乌克兰", "日本", "韩国", "朝鲜", "印度", "伊朗", "以色列",
    "巴勒斯坦", "黎巴嫩", "叙利亚", "伊拉克", "阿富汗", "也门", "巴基斯坦",
    "土耳其", "沙特", "阿联酋", "埃及", "利比亚", "苏丹", "索马里",
    "英国", "法国", "德国", "意大利", "西班牙", "葡萄牙", "荷兰", "比利时",
    "瑞士", "瑞典", "挪威", "丹麦", "芬兰", "波兰", "匈牙利", "奥地利",
    "捷克", "罗马尼亚", "塞尔维亚", "克罗地亚", "保加利亚", "希腊", "爱尔兰", "冰岛",
    "加拿大", "墨西哥", "巴西", "阿根廷", "智利", "秘鲁", "哥伦比亚",
    "委内瑞拉", "巴拿马", "古巴", "海地", "玻利维亚", "厄瓜多尔", "乌拉圭", "巴拉圭",
    "澳大利亚", "新西兰", "南非", "尼日利亚", "肯尼亚", "埃塞俄比亚", "加纳",
    "坦桑尼亚", "乌干达", "津巴布韦", "摩洛哥", "阿尔及利亚", "突尼斯",
    "菲律宾", "马来西亚", "印度尼西亚", "越南", "泰国", "新加坡", "缅甸",
    "柬埔寨", "老挝", "尼泊尔", "孟加拉", "斯里兰卡", "蒙古",
    "哈萨克斯坦", "乌兹别克斯坦", "亚美尼亚", "阿塞拜疆", "格鲁吉亚",
    "欧盟", "北约", "联合国", "白宫", "克里姆林宫", "五角大楼", "中东", "加沙",
]

# 热点词表里的"通用词"：国际要闻只命中这类词时，不该被抢进热点栏
# （如"伊朗革命卫队…打击违规船只"含"违规"、"巴拿马发生7级地震"含"地震"）
_WEAK_HOTSPOT_WORDS = [
    "违规", "违法", "回应", "引发", "争议", "通报", "网友", "视频", "热议",
    "地震", "台风", "暴雨", "洪水", "泥石流", "火灾", "爆炸", "事故",
    "遇难", "受伤", "救援", "搜救", "查处", "整治",
]


def _classify_by_title(title):
    """根据标题关键词推断分类，用于规则版兜底。返回国际/国内/热点/None。

    分类规则：
    1. 默认按命中词数比大小；平局时热点优先于国内/国际：只带一个国名的社会
       奇闻不该占要闻栏（"美国一男子婚礼上新郎被狗咬"→热点）。
    2. 例外：标题含明确外国实体，且热点侧只命中"通用词"（违规/争议/引发/地震/
       爆炸…）时判为国际——如"伊朗革命卫队…打击违规船只""美国直播枪决计划
       引发强烈争议""巴拿马发生7.6级强震"。国内词更多时不适用（避免把国内
       口径的新闻挪走）。
    """
    hits = {
        sec: [kw for kw in set(keywords) if kw in title]  # 词表有重复词，去重后计分
        for sec, keywords in TITLE_KEYWORDS.items()
    }
    scores = {sec: len(v) for sec, v in hits.items()}
    priority = {"热点": 2, "国内": 1, "国际": 0}
    best_sec = max(scores, key=lambda s: (scores[s], priority[s]))
    if scores[best_sec] == 0:
        return None
    if (
        scores["国际"] > 0
        and scores["国内"] <= scores["国际"]
        and hits["热点"]
        and all(kw in _WEAK_HOTSPOT_WORDS for kw in hits["热点"])
        and any(kw in title for kw in _FOREIGN_ANCHORS)
    ):
        return "国际"
    # 国际 vs 国内 同分且国际侧命中外国实体锚点 → 判国际。
    # 例："人物｜白宫新任发言人：曾在中国生活三年" 里的"中国"不该把它拉到国内栏
    if (
        scores["国际"] == scores["国内"] == scores[best_sec]
        and any(kw in title for kw in _FOREIGN_ANCHORS)
    ):
        return "国际"
    return best_sec


def build_prompt(news_items, per_section=None, per_line=None):
    """构造 LLM 提示词，明确分类标准和排除规则。"""
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    # 控制候选数量，避免小模型上下文过载（但不要太少，保证筛选空间）。
    # 注意：不能简单取前 N 条——采集顺序固定（新浪→百度→联合早报→天行→聚合），
    # 直接截断会让候选被单一来源占满、国际/热点栏无米下锅，导致 LLM"格式不达标"。
    # 改为按分类均衡采样：每类最多 20 条，保证三栏都有充足候选。
    buckets = {"国际": [], "国内": [], "热点": []}
    for n in news_items:
        sec = (
            _classify_by_title(n.get("title", ""))
            or CATEGORY_MAP.get(n.get("category", ""))
            or "热点"
        )
        buckets.setdefault(sec, []).append(n)
    sampled = []
    for sec in ("国际", "国内", "热点"):
        sampled.extend(buckets[sec][:20])
    if len(sampled) < 50:  # 不满再按原顺序从剩余补充
        seen_ids = {id(n) for n in sampled}
        for n in news_items:
            if len(sampled) >= 50:
                break
            if id(n) not in seen_ids:
                sampled.append(n)
                seen_ids.add(id(n))
    news_items = sampled
    def _fmt_time(n):
        t = n.get("time", "")
        if t and isinstance(t, str):
            return t[:10]  # 取 YYYY-MM-DD
        ctime = n.get("ctime", "")
        if ctime:
            try:
                from datetime import datetime
                return datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d")
            except Exception:
                pass
        return ""

    candidate = "\n".join(
        f"{i + 1}. 【{n['category']}】{_fmt_time(n)} {n['title']}"
        f"{'｜' + n['summary'][:80] if n.get('summary') else ''}"
        f"｜来源：{n['source']}"
        for i, n in enumerate(news_items)
    )
    # 动态日期：提示词里的"今天"必须跟随真实运行日期，不能写死
    _now = datetime.now(BJT)
    today_cn = f"{_now.year}年{_now.month}月{_now.day}日"
    today_iso = _now.strftime("%Y-%m-%d")
    yesterday_iso = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff_iso = (_now - timedelta(days=3)).strftime("%Y-%m-%d")
    return f"""你是资深新闻编辑。以下是今日从权威渠道采集的候选新闻（含标题、简介、来源）。

候选新闻：
{candidate}

请从中选出最有公共价值的 {per_section * 3} 条新闻（国际、国内、热点各 {per_section} 条），严格遵守以下规则：

【分类标准】
- 国际：外国政府决策、国际关系/外交、国际军事冲突、全球性重大事件、重大国际灾害事故。
  国际栏必须包含至少1条重大国际政治/军事新闻（如大国领导人动态、战争冲突、重大外交事件、制裁/停火/谈判）。
  国际栏严禁选入：外国公司IPO/财报/股价/销量、美联储/央行利率预测、债券市场分析、外国科技公司产品发布、加密货币、商品期货、文化交流活动。
- 国内：中国中央及地方政府的政策法规、宏观经济数据、重大科技突破、重大民生政策、重大事故/灾难。
  不要选入：个股涨跌、期货早报、研报、游戏、明星八卦、单家公司业绩。
- 热点：社会新闻（事故/案件/灾害/民生事件/消费者权益/食品安全）、全网热议的民生话题。
  热点优先选与民众生活相关的社会类新闻，娱乐明星绯闻/八卦不要选，体育赛事结果不要选。

【时间要求】
- 今天是{today_cn}。候选新闻前面标注了日期（如"{today_iso}"）。
- 优先选择标注日期为今天（{today_iso}）的新闻，其次是昨天（{yesterday_iso}）的。
- 严格排除日期在3天以前的新闻（{cutoff_iso}及更早），即使内容重要也不选。
- 如果标题里提到的具体事件日期是几个月前的（如"5月18日"、"6月26日"），这条是旧闻，必须排除。
- 排除历史回顾类内容：事件本身发生在数月/数年前，如今只是"披露/首次公开/纪录片/影像曝光"
  （如"央视披露多年前营救细节"），不是当日新闻，一律不选。

【写作要求】
1. 严格排除以下类型（即使标题很吸引眼球）：
   - 娱乐明星绯闻/八卦（如明星恋情、回应传闻、个人隐私）
   - 外国公司IPO/财报/股价/销量（如戴尔财报、希音IPO、丰田销量、苹果发布）
   - 股东增减持等个股资本运作（如"XX获XX大手笔增持XX万股"）
   - 个股/期货/ETF/研报、加密货币、彩票博彩
   - 游戏新闻、文化交流/展览、纯商品价格快讯、广告软文
   - 军方/官媒自媒体账号的口号式标题（如"中国军号：战争的走向由我说了算"这类只有口号、
     没有具体事实的标题）
   - 外国股市与资金流动类（如"外资加速撤离韩股""某国股市市值缩水""投资者抛售"）
   - 企业高管个人言论与人物专访/特写稿（如"某公司CEO呼吁…""专访某某""人物｜某某"），
     除非本身涉及政策法规变化
2. 同一事件只保留一条，不跨分类重复；标题高度相似也算重复。
   同一热点主线只保留最重要的一条：例如同一场冲突的多条节点进展（打击、爆炸、断交、
   伤亡等），最多选1条信息最全的，不要连续刷屏同一主题。
   例如：西藏泥石流事件只能出现在一个分类中，不能同时出现在国内和热点。
3. 每条输出两行：第一行是**要点**（一二十字的新闻核心，允许把真实标题精简压缩，
   但必须保留关键人名/国名/地名/数字）；第二行是**介绍**（一句话，20至{per_line}字左右、
   最长不超过120字，必须写出标题之外的具体事实：主体、时间、地点、数字、结果或表态）。
   严禁把要点换个说法复述一遍——例如要点"美军完成对伊朗军事目标打击"，
   介绍若写"美军对伊朗军事目标进行打击"就是零信息量的灌水，应换一条能写清楚的新闻。
4. 介绍必须是完整句子，严禁用省略号"…"或"......"截断结尾。
   介绍必须以句号"、感叹号"！"或问号"？"结尾，不能以逗号、顿号或单个汉字结尾。
   错误示例："日本防卫省甚至。"、"推动加快构建房地产发展新模。"、"比口岸高出1000米，可。"、"同比增长。"
   正确示例："日本防卫省甚至考虑允许招募文身者以解决人员短缺问题。"
5. 要点必须源自候选新闻的真实标题（允许精简压缩），不得编造不存在的新闻；介绍与要点必须属于同一条新闻。
6. 数量要求：【国际】【国内】【热点】三个分类每个至少输出2条，最多输出{per_section}条。不要只输出1条或0条。
7. 必须简体中文，严格按下面格式输出，不要输出任何解释、思考、前后缀：

【国际】
1. **一二十字的要点**
一句话，补充具体事实（时间/地点/数字/结果）。
2. **一二十字的要点**
一句话，补充具体事实。

【国内】
1. **一二十字的要点**
一句话，补充具体事实。
2. **一二十字的要点**
一句话，补充具体事实。

【热点】
1. **一二十字的要点**
一句话，补充具体事实。
2. **一二十字的要点**
一句话，补充具体事实。"""


def _do_request(provider, prompt):
    """执行单次 LLM 请求（内部函数，用于线程池超时控制）。"""
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": "你是资深新闻编辑，输出简洁、客观、有信息量。必须严格按照用户要求的格式输出，不要输出任何思考过程或解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        # 输出 15 条(标题+摘要)约 1200-1800 tokens，2500 足够；
        # 再大容易触发部分免费模型的 max_tokens 上限校验（400 Bad Request）
        "max_tokens": 3500,
    }
    if provider.get("extra_body"):
        payload.update(provider["extra_body"])

    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "Daily News",
    }
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    # 连接3秒，读取按provider配置；禁用urllib3重试，避免TCP异常时无限挂死
    timeout = provider.get("timeout", 60)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    resp = session.post(url, json=payload, headers=headers, timeout=(3, timeout))
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text.strip() and msg.get("reasoning_content"):
        text = msg["reasoning_content"]
    if isinstance(text, list):
        text = "".join(str(x) for x in text)
    # 过滤 <think> 思考过程
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
    if not text.strip():
        raise RuntimeError("返回空内容")
    return text


def _call_provider_with_retry(provider, prompt, max_retries=None):
    """调用 LLM provider，支持 429 限流的指数退避重试，并用线程池做硬超时保护。

    Returns: (text, error_message) 成功时 text 为响应内容，失败时 text 为 None。
    """
    if max_retries is None:
        max_retries = provider.get("max_retries", 1)
    hard_timeout = provider.get("timeout", 60) + 5  # 硬超时 = 读取超时 + 5秒缓冲
    last_error = None

    for attempt in range(max_retries + 1):
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_do_request, provider, prompt)
            text = future.result(timeout=hard_timeout)
            executor.shutdown(wait=False)
            return text, None
        except TimeoutError:
            last_error = f"硬超时({hard_timeout}s)"
            print(f"[summarize] {provider['model']} 请求超时({hard_timeout}s)")
            executor.shutdown(wait=False)
            # 免费服务偶发变慢，超时后重试一次（不是致命失败）
            if attempt < max_retries:
                time.sleep(RETRY_BASE_DELAY)
                continue
            return None, last_error
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            last_error = f"{status_code} {e.response.reason if e.response is not None else ''}"
            if status_code == 429 and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 限流(429)，{delay}s后重试(第{attempt+1}次)...")
                time.sleep(delay)
                continue
            if status_code == 402:
                return None, f"{status_code} Payment Required（欠费）"
            if status_code >= 500 and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 服务器错误({status_code})，{delay}s后重试...")
                time.sleep(delay)
                continue
            return None, f"{status_code} Client Error: {e.response.reason if e.response is not None else ''}"
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[summarize] {provider['model']} 调用异常，{delay}s后重试: {exc}")
                time.sleep(delay)
                continue
            return None, str(exc)
    return None, last_error


def _is_format_ok(sections, per_section=None):
    """三个分类都至少有1条就算格式达标（不足部分由 _fill_missing_sections 补充）。"""
    return all(len(sections.get(s, [])) >= 1 for s in SECTIONS)


def _is_chinese_ok(sections):
    """校验输出是否为中文（中文字符占比 >= 30%）。"""
    total = sum(len(t) + len(s) for items in sections.values() for t, s in items)
    if total == 0:
        return False
    cn = sum(
        len(re.findall(r"[\u4e00-\u9fff]", t + s))
        for items in sections.values()
        for t, s in items
    )
    return cn / total >= 0.3


def _is_summary_complete(sections, per_line=None):
    """校验摘要是否完整：不能以省略号结尾，不能太短，字数在合理范围内。"""
    per_line = per_line or NEWS_PER_LINE
    min_len = 15           # 最低字数（大幅降低，确保智谱稳定通过）
    max_len = 150          # 最高字数（再放宽）
    for sec, items in sections.items():
        for title, summary in items:
            s = summary.strip()
            if len(s) < min_len:
                print(f"[校验] 摘要太短：{len(s)}字 < {min_len}字，标题：{title[:20]}...")
                return False
            if len(s) > max_len:
                print(f"[校验] 摘要太长：{len(s)}字 > {max_len}字，标题：{title[:20]}...")
                return False
            if s.endswith("…") or s.endswith("...") or s.endswith("......"):
                return False
    return True


def _validate_titles_exist(sections, news_items):
    """校验 LLM 输出的标题是否在候选新闻中存在（模糊匹配，防止模型编造新闻）。

    使用更宽松的匹配：检查标题核心内容（前4个归一化字符）是否在候选中，
    允许模型对标题做适当润色/简化，但核心内容必须真实存在。
    至少 80% 的标题通过校验才算合格。
    """
    candidate_titles = [normalize_title(n["title"]) for n in news_items]
    total = 0
    matched = 0
    for sec, items in sections.items():
        for title, summary in items:
            total += 1
            norm = normalize_title(title)
            if len(norm) < 4:
                continue
            # 要点允许对原标题做精简压缩，判定放宽：
            # 1) 与任一候选标题的最长公共子串 >= 4 字 → 匹配
            # 2) 或共享数字（如"3800万"）且去数字后公共子串 >= 3 字 → 匹配
            hit = False
            for ct in candidate_titles:
                if len(ct) < 4:
                    continue
                if _longest_common_substring_len(norm, ct) >= 4:
                    hit = True
                    break
                nums = set(re.findall(r"\d+", norm)) & set(re.findall(r"\d+", ct))
                if nums and _longest_common_substring_len(
                    re.sub(r"\d+", "", norm), re.sub(r"\d+", "", ct)
                ) >= 3:
                    hit = True
                    break
            if hit:
                matched += 1
    if total == 0:
        return False
    # 允许个别要点有轻微改写/压缩，70%匹配即通过
    return matched / total >= 0.7


def _deduplicate_sections(sections):
    """去重：跨分类 + 同分类内相似标题去重。

    1. 跨分类：同一条新闻只保留在优先级最高的分类里（国内 > 国际 > 热点）。
    2. 同分类内：同一事件的不同报道（标题高度相似）只保留第一条。
    """
    priority = {"国内": 0, "国际": 1, "热点": 2}

    # 第一步：跨分类精确去重，按优先级决定归属
    seen_exact = {}
    for sec in sorted(sections, key=lambda s: priority.get(s, 99)):
        for title, summary in sections[sec]:
            norm = normalize_title(title)
            if norm not in seen_exact:
                seen_exact[norm] = (sec, title, summary)

    # 第二步：按优先级组装，全局同事件去重（跨分类+同分类内）
    # 同一事件即使被 LLM 分到不同分类、标题措辞不同，也只保留一条
    result = {sec: [] for sec in SECTIONS}
    used = []  # [(norm_title, summary)]
    for sec in sorted(SECTIONS, key=lambda s: priority.get(s, 99)):
        for norm, (keeper_sec, title, summary) in seen_exact.items():
            if keeper_sec != sec:
                continue
            is_dup = False
            for used_t, used_s in used:
                if _is_same_event(norm, summary, used_t, used_s):
                    is_dup = True
                    break
            if not is_dup:
                result[sec].append((title, summary))
                used.append((norm, summary))

    return {k: v for k, v in result.items() if v}


def _fill_missing_sections(sections, news_items, per_section=None):
    """当 LLM 返回的某些分类条数不足时，用规则筛选补充缺失的部分。"""
    per_section = per_section or NEWS_PER_SECTION
    # 已选用的标题（精确去重 + 同事件/同主线去重）
    used_titles = set()
    used_pairs = []  # [(norm_title, summary)]
    for items in sections.values():
        for title, summary in items:
            used_titles.add(normalize_title(title))
            used_pairs.append((normalize_title(title), summary or title))

    # 按分类从候选池中选取未被使用的、高重要性的新闻（同时过滤禁止内容）
    grouped = {"国际": [], "国内": [], "热点": []}
    for n in news_items:
        if normalize_title(n["title"]) in used_titles:
            continue
        # 过滤禁止关键词（和LLM输出用同一标准）
        text = n.get("title", "") + " " + n.get("summary", "")
        matched_ban = None
        for ban in _POST_FILTER_BAN:
            if ban in text:
                matched_ban = ban
                break
        if matched_ban:
            print(f"[补充过滤] {matched_ban}：{n['title'][:30]}...")
            continue
        sec = CATEGORY_MAP.get(n.get("category", ""))
        # 用标题关键词验证/修正分类（避免采集源分类错误）
        title_sec = _classify_by_title(n["title"])
        if title_sec and title_sec != sec:
            # 标题关键词强烈暗示另一分类，优先采用标题判断
            sec = title_sec
        if not sec:
            sec = "热点"
        if sec in grouped:
            grouped[sec].append(n)

    # 按重要性排序每组
    for sec in grouped:
        grouped[sec].sort(key=importance_score, reverse=True)

    def _fill_from(pool, current, needed):
        """从候选池补充条目，返回剩余缺口。与已选同事件/同主线的不补。"""
        for n in pool:
            if needed <= 0:
                break
            norm = normalize_title(n["title"])
            if norm in used_titles:
                continue
            if any(
                _is_same_event(norm, n.get("summary") or n["title"], u_t, u_s)
                for u_t, u_s in used_pairs
            ):
                print(f"[补充跳过] 与已选同主线：{n['title'][:30]}...")
                continue
            summary = _make_fill_summary(n, norm, news_items)
            if summary is None:
                continue
            current.append((n["title"], summary))
            used_pairs.append((norm, summary))
            used_titles.add(norm)
            needed -= 1
        return needed

    # 补充缺失分类或不足条数（同分类优先，不足时从其他分类补充）
    for sec in SECTIONS:
        current = sections.get(sec, [])
        needed = per_section - len(current)
        if needed > 0:
            needed = _fill_from(grouped.get(sec, []), current, needed)
            if needed > 0:
                # 同分类不够，从其他分类补充（热点不从国际补充）
                for other_sec in SECTIONS:
                    if needed <= 0:
                        break
                    if other_sec == sec:
                        continue
                    if sec == "热点" and other_sec == "国际":
                        continue
                    needed = _fill_from(grouped.get(other_sec, []), current, needed)
            sections[sec] = current
    return sections


def _borrow_ok(norm_a, norm_b):
    """借用简介的匹配闸：只在"标题层面"确认是同一事件时才允许借用。

    只用 _is_same_event 会借错（曾把"佩斯科夫表态俄罗斯"的简介借给
    "白宫新任发言人"这条，摘要与要点完全对不上）。
    """
    if _is_similar_title(norm_a, norm_b):
        return True
    if set(re.findall(r"\d+", norm_a)) & set(re.findall(r"\d+", norm_b)):
        return _longest_common_substring_len(norm_a, norm_b) >= 4
    return False


def _borrow_summary(n, norm, news_items):
    """本条无可用简介时，从其他来源找同一事件的报道，借用其简介。

    联合早报/微博热搜/聚合等源不带简介，但同一事件常被新浪/中新网/澎湃
    等带简介的源报道过，借用后可避免"裸标题"成行。找不到返回 None。
    """
    for m in news_items:
        if m is n or not m.get("summary"):
            continue
        if not _borrow_ok(norm, normalize_title(m["title"])):
            continue
        s = _rich_summary(m)
        if s:
            return s
    return None


def _make_fill_summary(n, norm, news_items=None):
    """为补充条目生成摘要；无可用内容返回 None（宁缺毋滥）。

    - 有实质简介优先；无简介时先尝试跨源借用同事件简介，
      再退回标题信息完整(>=9字)的以标题直接成行
      （联合早报等源无简介，重大国际/热点新闻不能因此漏掉）
    - 超长时在最近的句末标点处截断，找不到句子边界用标题兜底
    """
    summary = _rich_summary(n)
    if summary is None and news_items:
        summary = _borrow_summary(n, norm, news_items)
    if summary is None:
        if len(norm) >= 9:
            summary = n["title"]
        else:
            print(f"[补充跳过] 无简介且标题过短：{n['title'][:30]}...")
            return None
    if len(summary) > NEWS_PER_LINE:
        # 优先在最近的中文句号/感叹号/问号处截断
        cut = max(
            summary[:NEWS_PER_LINE].rfind("。"),
            summary[:NEWS_PER_LINE].rfind("！"),
            summary[:NEWS_PER_LINE].rfind("？"),
        )
        if cut > 0:
            summary = summary[:cut + 1]
        else:
            # 找不到句子边界，用标题兜底但保证语义完整
            summary = n["title"] + "。"
    return summary


def _reclassify_sections(sections):
    """根据标题关键词重新分类，修正LLM的分类错误。"""
    reclassified = {"国际": [], "国内": [], "热点": []}
    for sec, items in sections.items():
        for title, summary in items:
            new_sec = _classify_by_title(title)
            if not new_sec:
                new_sec = sec  # 如果无法判断，保持原分类
            reclassified[new_sec].append((title, summary))
    return reclassified


# 后处理：过滤LLM输出的明显问题（禁止内容、截断摘要等）
_POST_FILTER_BAN = [
    # 娱乐八卦
    "明星", "绯闻", "恋情", "分手", "出轨", "八卦", "网红", "综艺",
    "塌房", "官宣", "爆料", "婚变", "情变", "小三", "整容", "离婚",
    "孙宇晨", "景甜",
    # 外国公司IPO/财报/股价/销量（国际栏不应出现）
    "IPO", "上市", "财报", "营收", "净利润", "股价", "市值", "销量",
    "希音", "Shein", "丰田", "苹果发布", "特斯拉",
    # 车企/市场分析（不应作为国际政治新闻）
    "美国车企", "坚守中国市场", "汽车品牌", "美银证券",
    # 投资机构/债券分析（不应作为要闻）
    "品浩", "PIMCO", "债券收益率", "债市", "债券投资者", "消化不良", "沃什", "杰克逊霍尔",
    # 企业财报/业绩（不应作为要闻）
    "华泰证券", "齐鲁银行", "青农商行", "中期业绩", "净利润增长",
    "锦江酒店", "迈瑞医疗", "扣非净利", "归母净利润", "半年度报告",
    # 股市/券商/基金（不应作为要闻）
    "A股", "冲高回落", "大同证券", "华宝基金", "3A日报", "早盘", "领涨", "收绿",
    "涨停", "涨超", "跌超", "大涨", "大跌", "飙升", "暴跌",
    "板块", "逆市", "拉升", "农化", "粮食", "粮食需求",
    # 企业软广/公关
    "联想", "海尔", "小米发布", "华为发布", "北森", "北森CEO", "拼多多", "美团",
    "瑞为技术", "董事长", "签署战略合作协议",
    # 企业财报/业绩（补充）
    "理想汽车", "亏掉", "半年报", "收入", "同比下降",
    "董明珠", "格力电器", "家电龙头",
    # 财经基金
    "基金", "基民", "盈亏", "投资者占比", "中期报告",
    # 股市/投资（补充）
    "科创", "指数", "收益", "跑赢", "跑输",
    # 公司资本运作/外国芯片公司动态（非公共要闻）
    "拟投资", "战略投资", "融资", "收购", "并购", "入股",
    "英伟达", "NVIDIA", "超威半导体",
    # 上市公司公告/人事处罚（如"某公司公告：某高管被罚…未从公司领取薪酬"）
    "双汇", "领取薪酬", "公司公告", "业绩说明会", "股东大会", "股权激励",
    "信息披露", "监管函", "问询函",
    # 军方自媒体口号式标题（只有口号没有事实）
    "中国军号", "由我说了算", "勿谓言之不预", "玩火必自焚", "搬起石头砸自己的脚",
    # 外国股市/资金流动（外资撤离、抛售类）
    "外资加速撤离", "资金流出", "外资撤离", "投资者抛售",
    # 企业高管个人言论（非政策/法规变化）
    "首席执行官", "CEO呼吁",
]


def _post_filter_sections(sections):
    """后处理过滤：去除包含禁止关键词的新闻，去除明显截断的摘要。"""
    filtered = {}
    for sec, items in sections.items():
        kept = []
        for title, summary in items:
            text = title + summary
            # 过滤禁止关键词
            if any(ban in text for ban in _POST_FILTER_BAN):
                print(f"[后处理] 过滤禁止内容：{title[:30]}...")
                continue
            # 过滤明显截断的摘要（少于15字或以悬挂词结尾）
            s = summary.strip()
            if len(s) < 15:
                print(f"[后处理] 过滤截断摘要（{len(s)}字）：{title[:30]}...")
                continue
            # 以悬挂词结尾（句子未说完）
            if any(s.endswith(h) for h in HANGING_WORDS):
                print(f"[后处理] 过滤悬挂结尾：{title[:30]}...")
                continue
            # 以句号结尾但前面是悬挂词（如"甚至。"、"构。"）
            if s.endswith("。"):
                prefix = s[:-1]
                if any(prefix.endswith(h) for h in HANGING_WORDS):
                    print(f"[后处理] 过滤悬挂+句号：{title[:30]}... (结尾: ...{prefix[-5:]})")
                    continue
            # 摘要只是把标题换个说法复述（无信息增量）
            if _is_title_repeat(title, s):
                print(f"[后处理] 过滤标题复述摘要：{title[:30]}...")
                continue
            kept.append((title, summary))
        filtered[sec] = kept
    return filtered


def _is_title_repeat(title, summary):
    """判断摘要是否只是标题的同义复述（无信息增量，应视为灌水）。

    判定依据（归一化后）：
    - 两文本相等或互相包含 → 复述
    - 篇幅相近（差异在可接受范围内）且摘要的字符大多能在标题字符池中
      复用（复用率 >= 80%）→ 复述。无论重排、删词还是换少量说法，
      只要没有实质新词，都算"标题换了个说法"。
    - 摘要明显比标题长（补了大量新内容）→ 放行，不算复述
    """
    t = normalize_title(title)
    s = normalize_title(summary)
    if len(t) < 6 or len(s) < 6:
        return False  # 太短无法可靠判断
    if s == t or s.startswith(t) or t.startswith(s):
        return True
    ct = Counter(t)
    reused = sum(min(cnt, ct.get(ch, 0)) for ch, cnt in Counter(s).items())
    # 摘要开头就是标题的改写（前 len(t) 字里七成以上的字来自标题），
    # 整体篇幅也没比标题长多少 → "标题换了说法 + 半句套话"，
    # 如"美国制裁国际刑事法院" → "美国对国际刑事法院实施制裁，可能严重制约其运作能力。"
    head_part = s[: len(t)]
    head_reused = sum(
        min(cnt, ct.get(ch, 0)) for ch, cnt in Counter(head_part).items()
    )
    if (
        len(head_part) >= 6
        and head_reused >= len(head_part) * 0.7
        and len(s) <= len(t) * 2.5
    ):
        return True
    shorter_n = min(len(t), len(s))
    longer_n = max(len(t), len(s))
    # 篇幅差异过大说明补了新信息（或只截了一段），不判复述
    if longer_n - shorter_n > max(6, int(shorter_n * 0.6)):
        return False
    return reused >= len(s) * 0.75


def _strip_source_noise(text):
    """去掉源简介开头的电头 / 记者署名等噪声。

    如"中新网衡水10月10日电 (邢璐 王鹏 张凯)…"、"记者今天了解到，…"：
    规则兜底直接引用源简介时，这些噪声会原样进早报，必须清掉。
    """
    s = (text or "").strip()
    for _ in range(2):
        s = re.sub(
            r"^.{2,20}?\d{1,2}月\d{1,2}日电\s*(?:[（(][^)）]{0,40}[)）])?", "", s
        ).strip()
        s = re.sub(r"^记者[^，。；]{0,12}[，,：:]", "", s).strip()
    return s


def _rich_summary(n, per_line=None):
    """从候选条目提取可作摘要的实质简介；无可用内容返回 None。

    不可用的情形：简介为空、过短(<15字)、只是标题的同义复述、
    被平台截断成半句、或清掉电头后只剩口令碎片。
    返回 None 的条目不应强行选入（宁缺毋滥，避免"标题复述"式灌水摘要）。
    """
    per_line = per_line or NEWS_PER_LINE
    raw = (n.get("summary") or "").strip()
    if not raw:
        return None
    raw = raw.replace("**", "").replace("*", "").strip()
    raw = _strip_source_noise(raw)
    if len(raw) < 15:
        return None
    # 口令/序号碎片（如"“5、4、3、2、1，点火！"）不是新闻内容
    if re.match(r"^[“\"'（(]?\d+\s*[、,，]\s*\d+", raw):
        return None
    # 记者问答原文（"问：据报道…中方有何评论？"）不是新闻介绍
    if re.match(r"^[问答复]\s*[:：]", raw):
        return None
    if _is_title_repeat(n.get("title", ""), raw):
        return None
    # 源简介被平台硬截断成半句（较长却没有句末标点）→ 不可用，改用标题成行
    if len(raw) >= 35 and not re.search(r"[。！？]$", raw):
        return None
    return raw


_MAX_HEAD_LEN = 30

# 源栏目前缀（"人物｜…"、"国际特稿：…"）：在早报里是噪声，去掉
_COLUMN_PREFIX_RE = re.compile(
    r"^(?:国际|国内|要闻|社会)?(?:人物|专访|特稿|深度|观察|评论|视频|图集|直播)"
    r"\s*[｜|·:：]\s*"
)
# 尾段含这些词说明有信息量，不能当截断片段删掉
_KEEP_TAIL_WORDS = [
    "被", "将", "已", "获", "判", "罚", "刑拘", "拘留", "逮捕", "起诉", "判刑",
    "获刑", "道歉", "回应", "通报", "处罚", "调查", "启动", "死亡", "受伤",
    "遇难", "爆炸", "起火", "暂停", "取消", "恢复", "辞职", "免职", "签约",
]


def _strip_tail_fragment(head):
    """标题以"，短片段"结尾（源标题被截断/拼接）时去掉该尾段。

    如"小鹏当选…秘书长，何小鹏"、"女生校园内骑车被电动车撞倒离世，家属代理律师"。
    """
    m = re.search(r"[，,]\s*([^，,]{1,6})$", head)
    if not m:
        return head
    tail = m.group(1)
    if re.search(r"\d", tail) or any(w in tail for w in _KEEP_TAIL_WORDS):
        return head
    body = head[: m.start()]
    return body if len(body) >= 10 else head


def _shorten_head(head):
    """清理要点：去掉源栏目前缀 / 截断尾段；过长且含冒号时压成冒号前的核心部分。

    - LLM/采集源常把官方措辞整句抄进要点（如"东方甄选回应青虾仁净含量偏差争议：
      自然解冻的称重方法与现行法规要求的计量检测标准不一致"），压成冒号前短句更像"要点"
    - 采集源的栏目前缀（人物｜、专访｜）和截断尾段（"，何小鹏"）都该清掉
    """
    t = _COLUMN_PREFIX_RE.sub("", (head or "").strip())
    t = _strip_tail_fragment(t)
    if len(t) <= _MAX_HEAD_LEN:
        return t
    m = re.match(r"^(.{8,}?)[：:](.+)$", t)
    if m:
        # 截断到冒号前可能又露出尾段碎片（"…离世，家属代理律师：两车违停"），再清一次
        return _strip_tail_fragment(m.group(1).rstrip("，、；：,; "))
    return t


def sections_to_markdown(sections, per_line=None):
    """根据去重后的 sections 重新生成规范 markdown（简报格式：要点+介绍两行）。

    格式：
        1. **要点（一二十字）**
        介绍（一句话，补充具体事实）
    - 要点：标题或 LLM 压缩后的要点
    - 介绍：摘要；为空或只是要点复述时，只输出要点行
    """
    per_line = per_line or NEWS_PER_LINE
    lines = []
    for sec in SECTIONS:
        items = sections.get(sec, [])
        if not items:
            continue
        lines.append(f"【{sec}】")
        lines.append("")
        idx = 0
        for title, summary in items:
            # 清理要点（过长且带冒号的压成核心短句）
            t = _shorten_head(title.strip().strip("*").strip())
            # 清理介绍
            s = summary.strip()
            s = s.replace("**", "").replace("*", "").strip()
            s = re.sub(r"^介绍\s*[：:]\s*", "", s).strip()
            # 去掉尾部省略号（中英文省略号、连续句号等变体）
            s = re.sub(r"[…。.]{2,}\s*$", "", s).rstrip("…。.")
            # 修复明显截断：以逗号、顿号结尾的，去掉末尾标点
            while s and s[-1] in "，、；：":
                s = s[:-1].rstrip()
            idx += 1
            # 无实质介绍，或介绍只是要点复述 → 只输出要点行
            if not s or _is_title_repeat(t, s):
                lines.append(f"{idx}. **{t}**")
                lines.append("")
                continue
            # 介绍没有句末标点，补一个句号
            if not re.search(r"[。！？]$", s):
                s += "。"
            lines.append(f"{idx}. **{t}**")
            lines.append(s)
            lines.append("")
    return "\n".join(lines).strip()


def parse_markdown(text):
    """解析 LLM 输出的 markdown，返回 {分类: [(标题/要点, 摘要/介绍)]}。

    支持两种行格式：
    A. 新格式（简报两行）：
       1. **一二十字要点**
       一句话介绍。
    B. 旧格式（一行）：
       1. **标题**：摘要
    """
    sections = {}
    pos = 0
    while pos < len(text):
        m = re.search(r"【(国际|国内|热点)】", text[pos:])
        if not m:
            break
        sec = m.group(1)
        start = pos + m.end()
        next_m = re.search(r"【(国际|国内|热点)】", text[start:])
        end = start + next_m.start() if next_m else len(text)
        items = []
        lines = [ln.strip() for ln in text[start:end].splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            line = lines[i]
            mm = re.match(r"^\d+[\.、]\s*(.+?)$", line)
            if not mm:
                i += 1
                continue
            raw = mm.group(1).strip()
            # 旧格式一行："**标题**：摘要"（标题本身可能含"："，优先在 ** 闭合后切分）
            bold_kv = re.match(r"^\*\*(.+?)\*\*\s*[：:]\s*(.+)$", raw)
            if bold_kv:
                title = bold_kv.group(1).strip()
                summary = bold_kv.group(2).strip().rstrip("*").strip()
            elif "：" in raw or ":" in raw:
                sep = "：" if "：" in raw else ":"
                t, _, s = raw.partition(sep)
                title = t.strip().strip("*")
                summary = s.strip().strip("*").rstrip("*").strip()
            else:
                title = raw.strip("*").strip()
                summary = ""
                # 新格式两行：下一行非空且不以"数字."开头 → 作为介绍
                if i + 1 < len(lines) and not re.match(r"^\d+[\.、]", lines[i + 1]):
                    summary = lines[i + 1].strip()
                    i += 1
            # 剥掉模型可能照抄的"介绍："标签前缀
            summary = re.sub(r"^介绍\s*[：:]\s*", "", summary).strip()
            if title and summary:
                items.append((title, summary))
            i += 1
        if items:
            sections[sec] = items
        pos = start
    return sections


def _longest_common_substring_len(s1, s2):
    """最长公共子串长度。标题很短（几十字），O(n*m) 无性能问题。"""
    if not s1 or not s2:
        return 0
    prev = [0] * (len(s2) + 1)
    best = 0
    for i in range(1, len(s1) + 1):
        cur = [0] * (len(s2) + 1)
        ci = s1[i - 1]
        for j in range(1, len(s2) + 1):
            if ci == s2[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _is_similar_title(norm1, norm2, threshold=0.5):
    """判断两个归一化标题是否相似（同一事件的不同报道）。

    判定规则：
    1. 一个包含另一个 → 相似
    2. 最长公共子串占短标题比例 >= threshold → 相似
    3. 同事件指纹：共享核心短语(>=4字) + 共享数字 → 相似
       （如"冷藏车"事故的不同角度报道都含"8"，应视为同一事件）
    """
    if not norm1 or not norm2:
        return False
    if norm1 in norm2 or norm2 in norm1:
        return True
    short, long_ = (norm1, norm2) if len(norm1) <= len(norm2) else (norm2, norm1)
    lcs = _longest_common_substring_len(short, long_)
    if lcs >= 6 and lcs / len(short) >= threshold:
        return True
    # 同事件指纹：核心短语相同且数字相同（伤亡人数/年份等），即使报道角度不同
    # 如"冷藏车"(3字)事故的多篇报道都含"8"，应视为同一事件
    if lcs >= 3 and set(re.findall(r"\d+", norm1)) & set(re.findall(r"\d+", norm2)):
        return True
    return False


_BOILERPLATE_RE = re.compile(
    r"^(?:新浪|搜狐|网易|腾讯|央视|人民网|新华网|澎湃新闻|界面新闻|财联社)?"
    r"(?:科技|财经|数码|汽车|证券)?讯.*?消息[，,。]?"
)


def _clean_for_compare(s):
    """去掉"新浪科技讯8月25日晚间消息，"这类讯头，避免误判同事件。"""
    return _BOILERPLATE_RE.sub("", s or "").strip()


# 同一热点主线收敛用：主要国家/地区 + 动作域
# 用于避免同一主题连续刷屏（如美伊冲突的"打击""爆炸"两条节点只保留一条）
_MAINLINE_REGIONS = [
    "美国", "中国", "俄罗斯", "乌克兰", "日本", "韩国", "朝鲜", "印度",
    "伊朗", "以色列", "巴勒斯坦", "黎巴嫩", "叙利亚", "伊拉克", "阿富汗",
    "也门", "巴基斯坦", "土耳其", "沙特", "埃及", "利比亚", "缅甸",
    "英国", "法国", "德国", "秘鲁", "阿根廷", "巴西", "墨西哥",
    "加拿大", "澳大利亚", "欧盟", "北约", "联合国", "加沙", "吉隆",
    "西藏", "云南", "四川", "河南",
]
# 政要/名人实体：与地区一样可作为"同一主线"的锚点（如"特朗普向马斯克颁奖"无地区词）
_MAINLINE_PEOPLE = [
    "特朗普", "拜登", "普京", "泽连斯基", "金正恩", "马克龙", "马斯克",
    "黄仁勋", "内塔尼亚胡", "哈梅内伊", "莫迪", "石破茂", "李在明",
]
# 简称 → 全称：标题常用"美俄/中欧/美伊"这类缩写，只匹配全称会漏判同一主线
_MAINLINE_ALIASES = {
    "中美": ["中国", "美国"], "中俄": ["中国", "俄罗斯"], "中日": ["中国", "日本"],
    "中韩": ["中国", "韩国"], "中欧": ["中国", "欧盟"], "中英": ["中国", "英国"],
    "中印": ["中国", "印度"], "美俄": ["美国", "俄罗斯"], "美欧": ["美国", "欧盟"],
    "美伊": ["美国", "伊朗"], "美日": ["美国", "日本"], "美印": ["美国", "印度"],
    "俄乌": ["俄罗斯", "乌克兰"], "英美": ["英国", "美国"], "日韩": ["日本", "韩国"],
    "巴以": ["巴勒斯坦", "以色列"],
}
# 单字简称（"俄将向美…""对欧出口"这类标题完全没有全称）→ 全称
_MAINLINE_SHORT = {
    "美": "美国", "俄": "俄罗斯", "欧": "欧盟", "乌": "乌克兰", "伊": "伊朗",
}
_MAINLINE_DOMAINS = {
    "军事": ["打击", "空袭", "轰炸", "爆炸", "袭击", "遇袭", "遭袭", "突袭",
             "夜袭", "炮轰", "狂轰", "再轰", "导弹", "炮击", "开火", "交火",
             "反击", "击落", "撤军", "军事目标", "冲突", "战争"],
    "外交": ["断交", "绝交", "声明", "宣布", "谴责", "抗议", "会谈", "谈判",
             "会晤", "访问", "禁运", "制裁"],
    "灾害": ["地震", "台风", "洪水", "泥石流", "火灾", "事故", "死伤", "伤亡",
             "遇难", "丧生", "身亡", "受伤", "搜救", "救援", "抢通", "塌方",
             "失联", "被困", "冲毁"],
    "表彰": ["颁奖", "颁发", "颁授", "奖章", "勋章", "授予", "表彰", "获奖"],
    "能源": ["石油", "原油", "柴油", "汽油", "天然气", "液化气", "能源", "油价"],
    "贸易": ["关税", "贸易战", "贸易", "出口", "进口", "加征", "反倾销", "经贸"],
}


def _mainline_regions(text):
    """标题里的地区/政要锚点集合（含简称展开，如"美俄"→美国+俄罗斯）。"""
    tokens = {r for r in _MAINLINE_REGIONS + _MAINLINE_PEOPLE if r in text}
    for short, full in _MAINLINE_ALIASES.items():
        if short in text:
            tokens.update(full)
    for short, full in _MAINLINE_SHORT.items():
        if short in text:
            tokens.add(full)
    return tokens


def _same_mainline(t1, t2):
    """判断两条新闻是否属于同一热点主线（同一地区 + 同一动作域）。

    用于收敛同一事件的多个报道节点（如"美军打击伊朗"+"伊朗多地爆炸"），
    避免早报同一主题连续刷屏；不同动作域（军事 vs 外交）则各自保留。
    地区锚点支持简称展开，因此"美俄达成柴油协议"与"俄将向美…供应石油"
    （同属能源域）会收敛为一条。
    """
    if not (_mainline_regions(t1) & _mainline_regions(t2)):
        return False
    for domain_words in _MAINLINE_DOMAINS.values():
        if any(w in t1 for w in domain_words) and any(w in t2 for w in domain_words):
            return True
    return False


def _is_same_event(t1, s1, t2, s2):
    """判断两条新闻是否为同一事件（标题或摘要任一相似即算）。

    规则：
    1. 标题相似（_is_similar_title）→ 同事件
    2. 同事件指纹：共享数字（伤亡人数/年份等）+ >=4 字核心短语
       （标题或摘要，去数字后比较）。如"冷藏车"事故多篇报道都含"8"
       且摘要共享"河南平顶山"。无数字的套话（"相关部门已回应"）不会误判。
    3. 同一热点主线（_same_mainline：同一地区 + 同一动作域）→ 收敛为一条
    """
    if _same_mainline(t1, t2):
        return True
    if _is_similar_title(t1, t2):
        return True
    s1c, s2c = _clean_for_compare(s1), _clean_for_compare(s2)
    nums = set(re.findall(r"\d+", t1 + s1)) & set(re.findall(r"\d+", t2 + s2))
    if nums:
        lt = _longest_common_substring_len(re.sub(r"\d+", "", t1), re.sub(r"\d+", "", t2))
        ls = _longest_common_substring_len(re.sub(r"\d+", "", s1c), re.sub(r"\d+", "", s2c))
        if max(lt, ls) >= 4:
            return True
    # 特殊规则：共享地名+事件关键词（如"西藏泥石流"），即使没有共享数字也视为同一事件
    location_events = ["西藏泥石流", "吉隆泥石流", "西藏吉隆", "房地产信贷", "住房贷款", " Jackson Hole"]
    for le in location_events:
        if le in t1 + s1c and le in t2 + s2c:
            return True
    # 更通用：共享同一灾害类型+同一地区（如"泥石流"+"西藏"/"吉隆"）
    disasters = ["泥石流", "地震", "台风", "洪水", "雪崩", "堰塞湖"]
    for d in disasters:
        if d in t1 + s1c and d in t2 + s2c:
            # 同时共享地区关键词
            regions = ["西藏", "吉隆", "四川", "内江", "隆昌", "上海", "广东", "长沙", "重庆"]
            for r in regions:
                if r in t1 + s1c and r in t2 + s2c:
                    return True
    return False


def _one_line_summary(n, per_line):
    """规则兜底生成完整一句话新闻：完整性优先，字数目标 per_line 左右（30-100字）。

    若该条没有可用简介（空/过短/标题复述/源截断半句），返回 None，
    由调用方以标题直接成行（宁缺毋滥，绝不输出半句话）。
    """
    rich = _rich_summary(n, per_line)
    if rich is None:
        return None
    # 源数据常把简介硬截断成半句（如"…锁定在显示器分"、"…射击与MOBA"），
    # 原文没有句末标点，清理后补句号也读不通，直接判为不可用（改用标题成行）
    if len(rich) >= 35 and not re.search(r"[。！？]$", rich):
        return None
    s = _clean_for_compare(rich)
    s = s.replace("**", "").replace("*", "").strip()
    # 去掉尾部省略号/连续句号
    s = re.sub(r"[…。.]{2,}\s*$", "", s).rstrip("…。.")
    # 去掉结尾悬挂标点
    while s and s[-1] in "，、；：":
        s = s[:-1].rstrip()
    if not s:
        return None
    if s and not re.search(r"[。！？]$", s):
        s += "。"
    # 字数控制：最低20字，最高120字
    min_limit = 20
    max_limit = 120
    if len(s) < min_limit:
        # 太短不足以成句：宁缺毋滥，让调用方只用要点行。
        # （旧的"用标题拼一句"会产出"标题：重复内容"这种观感很差的行）
        return None
    if len(s) > max_limit:
        truncated = s[:max_limit]
        for punct in ["。", "！", "？", "；", "，"]:
            pos = truncated.rfind(punct)
            if pos >= max_limit * 0.5:
                truncated = truncated[:pos + 1]
                break
        else:
            truncated = truncated.rstrip("，。；：、！？,.!:;") + "。"
        s = truncated
    return s


def rule_summary(news_items, per_section=None, per_line=None):
    """无 LLM 时的降级方案：按分类+重要性评分筛选，生成一句话新闻。

    改进：
    - 使用 importance_score 对新闻排序（政治/重大事件优先）
    - 社会新闻归入"热点"
    - 跨分类模糊去重（同一事件不同报道只保留一条）
    - AI 全失效时信息量补偿：每栏取 per_section + FALLBACK_EXTRA_PER_SECTION 条
    """
    per_section = per_section or NEWS_PER_SECTION
    per_line = per_line or NEWS_PER_LINE
    total = per_section + FALLBACK_EXTRA_PER_SECTION

    # 规则筛选也用 _POST_FILTER_BAN 过滤禁止内容
    filtered_news = []
    for n in news_items:
        text = n.get("title", "") + " " + n.get("summary", "")
        if any(ban in text for ban in _POST_FILTER_BAN):
            continue
        filtered_news.append(n)

    grouped = {"国际": [], "国内": [], "热点": []}
    for n in filtered_news:
        # 优先用标题关键词推断分类（覆盖采集源的错误分类）
        sec = _classify_by_title(n["title"])
        # 如果标题关键词无法判断，再用采集源分类
        if not sec:
            sec = CATEGORY_MAP.get(n.get("category", ""))
        # 社会新闻强制归热点
        if n.get("category") == "社会":
            sec = "热点"
        # 还是不确定的，默认归"热点"
        if not sec:
            sec = "热点"
        grouped[sec].append(n)

    # 每组按重要性分数降序排列；有实质简介的优先，无简介的排最后（宁缺毋滥）
    for sec in grouped:
        grouped[sec].sort(
            key=lambda n: importance_score(n) + (60 if _rich_summary(n) else -60),
            reverse=True,
        )

    # 跨组去重（同一新闻/同一事件可能因分类模糊被放入多组）
    used = []  # [(norm_title, summary)]
    result = {"国际": [], "国内": [], "热点": []}

    def _is_duplicate(n):
        norm = normalize_title(n["title"])
        summary = n.get("summary") or ""
        for used_t, used_s in used:
            if _is_same_event(norm, summary, used_t, used_s):
                return True
        return False

    # 按优先级选：国内→国际→热点
    # 富简介优先；无简介条目只在富条不足每栏目标数时兜底（避免复述标题灌水）
    rich_counts = {"国际": 0, "国内": 0, "热点": 0}
    for sec in ["国内", "国际", "热点"]:
        for n in grouped[sec]:
            if len(result[sec]) >= total:
                break
            norm = normalize_title(n["title"])
            if _is_duplicate(n):
                continue
            if _rich_summary(n) is None and rich_counts[sec] >= per_section:
                continue
            used.append((norm, n.get("summary") or ""))
            result[sec].append(n)
            if _rich_summary(n) is not None:
                rich_counts[sec] += 1

    # 如果某组不足，从其他组的溢出新闻中按分数补充
    # 热点栏禁止从国际组补充（避免国际政治/灾难新闻混入热点）
    for sec in SECTIONS:
        if len(result[sec]) < total:
            for other_sec in SECTIONS:
                if other_sec == sec:
                    continue
                if sec == "热点" and other_sec == "国际":
                    continue  # 国际新闻不补到热点栏
                for n in grouped[other_sec]:
                    if len(result[sec]) >= total:
                        break
                    norm = normalize_title(n["title"])
                    if _is_duplicate(n):
                        continue
                    if _rich_summary(n) is None and rich_counts[sec] >= per_section:
                        continue
                    result[sec].append(n)
                    if _rich_summary(n) is not None:
                        rich_counts[sec] += 1
                    used.append((norm, n.get("summary") or ""))

    # 生成 markdown（简报格式：要点行 + 介绍行；富简介优先，不足时标题兜底）
    lines = []
    for sec in SECTIONS:
        items = result[sec]
        if not items:
            continue
        lines.append(f"【{sec}】")
        lines.append("")
        idx = 0
        for n in items[:total]:
            s = _one_line_summary(n, per_line)
            idx += 1
            lines.append(f"{idx}. **{_shorten_head(n['title'])}**")
            if s is None:
                # 无实质简介：只输出要点行
                lines.append("")
            else:
                lines.append(s)
                lines.append("")
    return "\n".join(lines).strip()


def summarize(news_items):
    """对候选新闻做 AI 筛选+提炼，返回最终 markdown 文本。

    流程：
    1. 依次尝试每个 LLM provider（带重试）
    2. 调用成功后解析输出，做格式/中文/完整性/真实性校验
    3. 格式不达标时，用强提示重试 1 次
    4. 校验通过则补充缺失分类（如有），返回结果
    5. 全部 LLM 失败，退回改进版规则筛选
    """
    global LAST_ENGINE
    if not news_items:
        return ""

    prompt = build_prompt(news_items)
    # 格式不达标时的修复提示
    fix_prompt = prompt + (
        f"\n\n【重要提醒】你上次输出格式不正确或内容不完整，请严格按上面的格式重新输出，"
        f"确保【国际】【国内】【热点】三个分类每个至少2条、最多{NEWS_PER_SECTION}条新闻，"
        f"每条输出两行：第一行「数字. **要点**」（一二十字的新闻核心，可压缩真实标题），"
        f"第二行是一句话介绍（20-60字，必须写出标题之外的具体事实：谁/何时/何地/结果或数字），"
        f"介绍必须是完整句子、不要用省略号结尾。"
        f"严禁把要点换个说法复述一遍；某条写不出实质内容就换另一条候选。"
        f"国际栏必须包含至少1条重大国际政治/军事新闻。"
    )

    best_partial = None   # 校验未完全通过时，条数最多的部分结果（可补充后采用）
    best_count = 0

    for provider in LLM_PROVIDERS:
        if not provider.get("api_key"):
            continue
        # 第一次尝试
        text, err = _call_provider_with_retry(provider, prompt)
        if text is None:
            print(
                f"[summarize] {provider['base_url']} {provider.get('model')} "
                f"调用失败：{err}"
            )
            continue

        # 解析并校验
        if "【国际】" in text:
            text = text[text.find("【国际】"):]
        sections = parse_markdown(text)
        sections = _deduplicate_sections(sections)
        total = sum(len(v) for v in sections.values())
        if total > best_count:
            best_count = total
            best_partial = sections

        format_ok = _is_format_ok(sections)
        chinese_ok = _is_chinese_ok(sections)
        complete_ok = _is_summary_complete(sections)
        titles_ok = _validate_titles_exist(sections, news_items)

        # 如果格式不达标（缺分类/条数不够），重试一次
        if not format_ok and provider.get("max_retries", 0) >= 0:
            print(f"[summarize] {provider['model']} 格式不达标，使用强提示重试1次...")
            text2, err2 = _call_provider_with_retry(provider, fix_prompt, max_retries=0)
            if text2 and "【国际】" in text2:
                text2 = text2[text2.find("【国际】"):]
                sections2 = parse_markdown(text2)
                sections2 = _deduplicate_sections(sections2)
                total2 = sum(len(v) for v in sections2.values())
                if total2 > best_count:
                    best_count = total2
                    best_partial = sections2
                if (_is_format_ok(sections2) and _is_chinese_ok(sections2)
                        and _is_summary_complete(sections2)):
                    sections = sections2
                    format_ok = True
                    chinese_ok = True
                    complete_ok = True
                    titles_ok = _validate_titles_exist(sections, news_items)

        if format_ok and chinese_ok and complete_ok and titles_ok:
            # 补充可能的缺失条目（如果某类不足）
            # 后处理过滤禁止内容和截断摘要
            sections = _post_filter_sections(sections)
            # 重新分类（修正LLM的分类错误）
            sections = _reclassify_sections(sections)
            # 补充缺失分类
            sections = _fill_missing_sections(sections, news_items)
            # 补充后可能引入重复，再去重一次
            sections = _deduplicate_sections(sections)
            print(
                f"[summarize] 已采用 {provider['base_url']} "
                f"{provider['model']} 的提炼结果"
            )
            LAST_ENGINE = "llm"
            return sections_to_markdown(sections)
        else:
            reasons = []
            if not format_ok:
                # 打印各分类条数，方便调试
                counts = {s: len(sections.get(s, [])) for s in SECTIONS}
                reasons.append(f"格式不达标(条数{counts})")
            if not chinese_ok:
                reasons.append("非中文")
            if not complete_ok:
                reasons.append("摘要截断")
            if not titles_ok:
                reasons.append("标题异常")
            print(
                f"[summarize] {provider['model']} {','.join(reasons)}，"
                "尝试下一个 provider"
            )

    # 所有 provider 都没完全达标：若有可用的部分结果（>=3条且标题真实），
    # 走"重分类 + 后处理 + 规则补充"后采用。
    # 常见场景：模型漏输出国际栏（国际0条/国内4条），此时整篇丢弃太可惜，
    # 重新分类后由规则把国际栏补齐即可，避免直接退回全规则版。
    if (
        best_partial
        and best_count >= 3
        and _validate_titles_exist(best_partial, news_items)
    ):
        print(
            f"[summarize] 无 provider 完全达标，采用条数最多的部分结果"
            f"({best_count}条)并重新分类/补充缺失栏位"
        )
        sections = _post_filter_sections(best_partial)
        sections = _reclassify_sections(sections)
        sections = _fill_missing_sections(sections, news_items)
        sections = _deduplicate_sections(sections)
        LAST_ENGINE = "llm"
        return sections_to_markdown(sections)

    print("[summarize] 所有 LLM 均不可用或格式不达标，退回规则筛选")
    LAST_ENGINE = "rule"
    return rule_summary(news_items)


if __name__ == "__main__":
    from collectors import collect_all
    from filter import filter_quality, filter_fresh, load_seen

    items = collect_all()
    items = filter_quality(items)
    print(f"质量过滤后：{len(items)} 条")
    print(summarize(items))





