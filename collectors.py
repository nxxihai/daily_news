"""多源新闻采集：百度热搜（免key）/ 天行数据 / 聚合数据 / 新浪滚动新闻。

每个采集器独立，失败只影响自身，collect_all() 汇总所有可用源的数据。
返回的新闻统一为字典：
    {"title": 标题, "summary": 摘要/描述, "source": 来源, "category": 分类}
分类取值：国际要闻 / 国内 / 社会 / 财经 / 科技 / 军事 / 热点

注意：外国科技公司名（苹果/特斯拉/英伟达等）不等于国际要闻，
不应仅因为标题出现公司名就归为国际新闻。国际要闻是政治/外交/军事/重大社会事件。
"""

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
BJT = timezone(timedelta(hours=8))  # 北京时间

import requests
import urllib3

# 本地证书库过期时 _get 会回退 verify=False，关闭对应的警告刷屏
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import JUHE_KEY, TIANAPI_KEY

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _get(url, params=None, timeout=12, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    try:
        resp = requests.get(url, params=params, headers=h, timeout=timeout)
    except requests.exceptions.SSLError:
        # 本地证书库过期时回退不校验证书（GitHub Actions 等云端环境不会走到这）
        resp = requests.get(
            url, params=params, headers=h, timeout=timeout, verify=False
        )
    resp.raise_for_status()
    return resp


# ---------- 百度实时热搜（免 key） ----------

BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"


def collect_baidu_hot():
    """抓取百度实时热搜榜，全部标为「热点」（社会新闻/热议话题为主）。"""
    resp = _get(BAIDU_HOT_URL)
    html = resp.text

    # 页面里热搜数据以内嵌 JSON 形式放在 <!--s-data:{...}--> 中
    m = re.search(r"<!--s-data:(\{.*?\})-->", html, re.S)
    if not m:
        raise ValueError("百度热搜页面解析失败（页面结构可能已改版）")

    data = json.loads(m.group(1))
    news = []
    for card in data.get("data", {}).get("cards", []):
        for item in card.get("content", []):
            title = str(item.get("word", "")).strip()
            if not title:
                continue
            news.append(
                {
                    "title": title,
                    "summary": str(item.get("desc", "")).strip(),
                    "source": "百度热搜",
                    "category": "热点",
                }
            )
    return news


# ---------- 新浪滚动新闻（免 key，国际/国内/财经/科技/社会） ----------

SINA_ROLL_URL = "https://feed.mix.sina.com.cn/api/roll/get"
SINA_HEADERS = {"User-Agent": UA, "Referer": "https://news.sina.com.cn/"}

# 【重要】国际要闻关键词：仅政治、外交、军事、重大国际事件
# 不要包含外国科技公司名（苹果/特斯拉/英伟达/三星等）——那是财经/科技新闻，不是国际要闻
INTERNATIONAL_KEYWORDS = [
    "美国", "英国", "法国", "德国", "俄罗斯", "日本", "韩国", "朝鲜",
    "欧盟", "北约", "伊朗", "以色列", "乌克兰", "印度", "澳大利亚",
    "加拿大", "土耳其", "沙特", "泰国", "越南", "新加坡", "巴西", "墨西哥",
    "巴勒斯坦", "黎巴嫩", "叙利亚", "伊拉克", "阿富汗", "巴基斯坦",
    "南非", "埃及", "尼日利亚", "肯尼亚", "印尼", "菲律宾", "马来西亚",
    "意大利", "西班牙", "葡萄牙", "荷兰", "比利时", "瑞士", "瑞典", "挪威",
    "丹麦", "芬兰", "波兰", "匈牙利", "奥地利", "希腊", "捷克", "罗马尼亚",
    "阿根廷", "智利", "秘鲁", "哥伦比亚", "委内瑞拉", "古巴",
    "特朗普", "拜登", "普京", "马克龙", "泽连斯基", "内塔尼亚胡", "金正恩",
    "莫迪", "苏纳克", "岸田", "朔尔茨", "埃尔多安", "哈梅内伊",
    "联合国", "世卫", "世贸", "国际货币基金", "世界银行",
    "白宫", "克里姆林宫", "五角大楼", "国会", "参议院", "众议院",
    "唐宁街", "爱丽舍宫", "青瓦台", "首相官邸",
    "美元", "欧元", "日元", "英镑", "人民币汇率",
    "全球", "国际", "海外", "中东", "非洲", "拉美", "东南亚", "南亚",
    "外交", "大使", "领事", "条约", "协议", "峰会", "会晤", "访问",
    "制裁", "关税", "贸易战", "冲突", "战争", "军事", "空袭", "导弹",
    "核武", "核试验", "恐怖袭击", "人质", "难民", "移民", "政变",
    "地震", "海啸", "火山", "飓风", "台风", "洪水", "灾荒",
    "G7", "G20", "APEC", "金砖", "上合", "东盟", "非盟",
]

# 国内社会新闻关键词（用于新浪分类修正）
DOMESTIC_SOCIAL_KEYWORDS = [
    "警方", "通报", "抓获", "破获", "案件", "事故", "遇难", "受伤",
    "火灾", "爆炸", "车祸", "坠楼", "跳楼", "溺水", "失踪", "失联",
    "救援", "搜救", "被困", "获救", "地震", "台风", "暴雨", "洪水",
    "幼儿园", "小学", "中学", "大学", "医院", "医生", "护士", "患者",
    "食品", "药品", "疫苗", "假货", "诈骗", "传销", "盗窃", "抢劫",
    "城管", "拆迁", "物业", "业主", "维权", "上访", "举报", "处罚",
    "工资", "拖欠", "裁员", "失业", "就业", "社保", "医保", "养老",
]


def _sina_category(title, default_cat):
    """根据标题关键词修正新浪新闻分类。

    - 国际关键词 → 国际要闻（但排除明显是国内社会/财经/文化/体育新闻）
    - 社会新闻关键词 → 社会（归入热点栏）
    - 其他保留源分类
    """
    # 排除明显不是国际要闻的情况（即使有国际关键词）
    # 注意：这些词单独出现不一定代表国内新闻，需结合上下文判断
    NOT_INTERNATIONAL = [
        "停车", "纠纷", "涉嫌", "贪腐", "被辞退", "被立案", "留置", "违纪",
        "文化之春", "文化交流", "文化年", "艺术节", "展览", "画展", "演出",
        "销量", "财报", "营收", "净利润", "盈利", "亏损", "股价", "市值",
        "夺冠", "奥运会", "奥运", "网球", "篮球", "足球", "冠军", "亚军",
        # "招募" "招聘" 去掉——日本/美国军队招募是国际新闻
    ]
    if any(kw in title for kw in NOT_INTERNATIONAL):
        # 有排除词，不归国际要闻，按原分类或社会/国内处理
        if default_cat in ("国内", "社会"):
            return "社会"
        return "国内"

    # 检查是否真的是国际要闻
    intl_hits = sum(1 for kw in INTERNATIONAL_KEYWORDS if kw in title)
    if intl_hits >= 1:
        return "国际要闻"
    # 社会新闻判断（只对"国内"频道的新闻做修正，不改变其他频道分类）
    social_hits = sum(1 for kw in DOMESTIC_SOCIAL_KEYWORDS if kw in title)
    if social_hits >= 1 and default_cat in ("国内", "社会"):
        return "社会"
    return default_cat


def collect_sina():
    """新浪滚动新闻接口（免 key）。

    经实际测试，正确 lid 对应频道：
      2511 国际要闻 / 2514 国际军事 / 2510 国内综合 / 2669 社会
      2509 财经 / 2515 科技
    错误 lid（废弃）：2512彩票 / 2513娱乐 / 2516商品期货 / 2517港股 / 2518加密货币
    """
    import random

    news = []
    # 频道配置：(lid, 默认分类, 条数)
    channels = [
        ("2511", "国际要闻", 15),  # 国际要闻（主）
        ("2514", "国际要闻", 10),  # 国际军事/外交
        ("2510", "国内", 15),      # 国内综合
        ("2669", "社会", 15),      # 社会/热点
        ("2509", "财经要闻", 8),   # 财经（少量，个股已被filter过滤）
        ("2515", "科技", 8),       # 科技（少量）
    ]
    for lid, default_cat, num in channels:
        try:
            # 走 _get：本地证书/代理导致的 SSL 失败会自动回退 verify=False
            resp = _get(
                SINA_ROLL_URL,
                params={
                    "pageid": "153",
                    "lid": lid,
                    "k": "",
                    "num": str(num),
                    "page": "1",
                    "_": str(random.randint(10**13, 10**14)),
                },
                headers=SINA_HEADERS,
                timeout=12,
            )
            data = resp.json().get("result", {}).get("data", [])
            for item in data:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                # 根据标题关键词修正分类
                cat = _sina_category(title, default_cat)
                # 提取时间字段（ctime 是时间戳，time 是格式化时间）
                ctime = item.get("ctime", "")
                time_str = item.get("time", "")
                news.append(
                    {
                        "title": title,
                        "summary": str(item.get("intro", "") or "").strip(),
                        "source": "新浪新闻",
                        "category": cat,
                        "ctime": ctime,
                        "time": time_str,
                    }
                )
        except Exception as exc:
            print(f"[sina] lid {lid} 采集失败：{exc}")
    return news


# ---------- 天行数据（需 key，综合新闻接口） ----------
# 接口文档：https://www.tianapi.com/apiview/235  （综合新闻查询）
TIANAPI_NEWS_URL = "https://apis.tianapi.com/generalnews/index"


def _tianapi_category(title):
    """天行数据无分类字段，用关键词粗分。"""
    intl_hits = sum(1 for kw in INTERNATIONAL_KEYWORDS if kw in title)
    social_hits = sum(1 for kw in DOMESTIC_SOCIAL_KEYWORDS if kw in title)
    if intl_hits > social_hits:
        return "国际"
    if social_hits > 0:
        return "社会"
    return "国内"


def collect_tianapi():
    """天行综合新闻接口 generalnews：一次拉取 30 条最新资讯。"""
    if not TIANAPI_KEY:
        return []
    news = []
    try:
        resp = _get(TIANAPI_NEWS_URL, params={"key": TIANAPI_KEY, "num": 30})
        data = resp.json()
        if data.get("code") != 200:
            print(f"[tianapi] generalnews 返回错误：{data.get('msg')}")
            return []
        for item in data["result"]["newslist"]:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            news.append(
                {
                    "title": title,
                    "summary": str(item.get("description", "") or "").strip(),
                    "source": str(item.get("source", "") or "天行数据"),
                    "category": _tianapi_category(title),
                    # 天行免费接口常返回陈年缓存新闻，必须带时间字段供 filter 按日期硬过滤
                    "ctime": str(item.get("ctime", "") or ""),
                }
            )
    except Exception as exc:
        print(f"[tianapi] generalnews 采集失败：{exc}")
    return news


# ---------- 聚合数据（需 key，头条新闻列表） ----------
# 接口文档：https://www.juhe.cn/docs/api/id/235 （头条新闻）
# 注意：聚合的 key 按接口开通；此 key 已开通"头条新闻"，未开通"AI新闻简报"
JUHE_NEWS_URL = "https://v.juhe.cn/toutiao/index"
# type 映射：头条新闻的 type 值 → 早报分类或"_skip"（直接跳过）
JUHE_TYPE_MAP = {
    "top": "国内",        # 头条
    "guonei": "国内",
    "guoji": "国际要闻",
    "shehui": "社会",
    "junshi": "国际",     # 军事归国际
    "keji": "科技",
    "caijing": "财经",
    "yule": "_skip",      # 娱乐直接跳过
    "tiyu": "_skip",      # 体育跳过
    "youxi": "_skip",     # 游戏跳过
    "qiche": "科技",      # 汽车归科技
}


def collect_juhe():
    """聚合数据头条新闻接口，按分类各取少量（节省免费版额度）。"""
    if not JUHE_KEY:
        print("[juhe] 未配置 JUHE_KEY，跳过聚合数据（https://www.juhe.cn/ 注册后获取）")
        return []
    news = []
    for raw_type, default_cat in JUHE_TYPE_MAP.items():
        if default_cat == "_skip":
            continue
        try:
            resp = _get(
                JUHE_NEWS_URL,
                params={"key": JUHE_KEY, "type": raw_type, "page_size": 10},
                timeout=10,
            )
            data = resp.json()
            if data.get("error_code") != 0:
                print(f"[juhe] type={raw_type} 返回错误：{data.get('reason')}")
                continue
            items = (data.get("result") or {}).get("data") or []
            for item in items:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                news.append(
                    {
                        "title": title,
                        "summary": "",
                        "source": str(item.get("author_name", "聚合数据") or "聚合数据").strip(),
                        "category": default_cat,
                        # 聚合头条返回 date 字段（如 2026-08-30 08:00），带上供日期硬过滤
                        "ctime": str(item.get("date", "") or ""),
                    }
                )
        except Exception as exc:
            print(f"[juhe] type={raw_type} 采集失败：{exc}")
    print(f"[juhe] 采到 {len(news)} 条")
    return news


# ---------- 联合早报（免 key，新加坡主流华文媒体，国际视角好） ----------
# 官网 RSS 已停用，改为抓取即时频道页；国内可直连，失败不影响其他源

ZAOBAO_PAGES = [
    ("https://www.zaobao.com/realtime/china", "国内"),
    ("https://www.zaobao.com/realtime/world", "国际要闻"),
]

# 正文链接形如 /news/china/story20260830-9597155，日期(YYYYMMDD)嵌在URL中
_ZAOBAO_STORY_RE = re.compile(
    r'<a[^>]+href="(/news/(?:china|world)/story(\d{8})-\d+)"[^>]*>(.*?)</a>',
    re.S,
)


def collect_zaobao():
    """抓取联合早报即时频道页，提取标题和日期（供日期硬过滤）。"""
    news = []
    for url, cat in ZAOBAO_PAGES:
        try:
            resp = _get(url, timeout=12)
            resp.encoding = "utf-8"
            seen = set()
            for m in _ZAOBAO_STORY_RE.finditer(resp.text):
                href, date8, inner = m.groups()
                if href in seen:
                    continue
                seen.add(href)
                # 标题在 <a> 内，需去掉 lit 注释和 HTML 标签
                title = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
                title = re.sub(r"<[^>]+>", "", title)
                title = re.sub(r"\s+", " ", title).strip()
                if len(title) < 6:
                    continue
                # URL 中的日期转时间戳，供 filter 按日期硬过滤
                try:
                    ts = str(int(datetime.strptime(date8, "%Y%m%d").timestamp()))
                except ValueError:
                    ts = ""
                news.append(
                    {
                        "title": title,
                        "summary": "",
                        "source": "联合早报",
                        "category": cat,
                        "ctime": ts,
                    }
                )
        except Exception as exc:
            print(f"[zaobao] {url} 采集失败：{exc}")
    return news


# ---------- 微博热搜（免 key，热点栏主力，时效最快） ----------
# 接口：微博移动端侧边栏热搜，返回实时热词+热度值，无需登录
WEIBO_HOT_URL = "https://weibo.com/ajax/side/hotSearch"


def collect_weibo_hot():
    """抓取微博实时热搜，全部标为「热点」，带热度值供重要性排序。"""
    resp = _get(
        WEIBO_HOT_URL,
        headers={"Referer": "https://weibo.com/",
                 "X-Requested-With": "XMLHttpRequest"},
    )
    data = resp.json().get("data", {})
    news = []
    for item in data.get("realtime", [])[:40]:
        # note 是展示标题（可能带 #话题#），word 是搜索词；优先 note
        title = str(item.get("note") or item.get("word", "")).strip().strip("#")
        if not title or item.get("is_ad"):
            continue
        news.append(
            {
                "title": title,
                "summary": "",
                "source": "微博热搜",
                "category": "热点",
                "heat": int(item.get("num", 0) or 0),  # 热度值，供 filter 排序参考
            }
        )
    return news


# ---------- 澎湃新闻（免 key，编辑质量高，详情页 meta 取简介） ----------
# 首页右侧聚合接口：热榜/精选/财经快讯，含时间戳；频道接口有反爬不可用
THEPAPER_INDEX_URL = "https://api.thepaper.cn/contentapi/wwwIndex/rightSidebar"
THEPAPER_DETAIL_URL = "https://www.thepaper.cn/newsDetail_forward_{}"
_THEPAPER_META_RE = re.compile(
    r'<meta[^>]+name="description"[^>]+content="([^"]+)"'
)


def _thepaper_summary(cont_id):
    """抓详情页 meta description 作简介，失败返回空串。"""
    try:
        r = _get(THEPAPER_DETAIL_URL.format(cont_id), timeout=8)
        r.encoding = "utf-8"
        m = _THEPAPER_META_RE.search(r.text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    except Exception:
        pass
    return ""


def _thepaper_category(title):
    """澎湃无分类字段，复用关键词粗分。"""
    return _tianapi_category(title)


def collect_thepaper():
    """澎湃首页聚合（热榜+精选+快讯），并发抓详情页简介。"""
    resp = _get(THEPAPER_INDEX_URL)
    data = resp.json().get("data", {})
    pools = (
        data.get("hotNews", [])
        + data.get("editorHandpicked", [])
        + data.get("financialInformationNews", [])
    )
    items, seen = [], set()
    for it in pools:
        title = str(it.get("name", "")).strip()
        cid = str(it.get("contId", ""))
        if not title or cid in seen:
            continue
        seen.add(cid)
        items.append(
            {
                "title": title,
                "summary": "",
                "source": "澎湃新闻",
                "category": _thepaper_category(title),
                "ctime": str(int(it.get("pubTimeLong", 0) / 1000) or ""),
                "_cid": cid,
            }
        )
    items = items[:25]
    # 并发抓详情页简介（本地证书问题，cloud/Actions 正常；失败则留空，靠跨源借用兜底）
    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = list(pool.map(lambda n: _thepaper_summary(n["_cid"]), items))
    for n, s in zip(items, summaries):
        n["summary"] = s
        n.pop("_cid", None)
    return items


# ---------- 中国新闻网即时 RSS（免 key，时政/国内/社会权威源，带简介） ----------
CHINANEWS_RSS_URL = "https://www.chinanews.com.cn/rss/scroll-news.xml"

# RSS 链接路径 → 早报分类；不在表内的频道（生活/健康/娱乐等）跳过
_CHINANEWS_CAT_MAP = {
    "/gj/": "国际要闻",
    "/gn/": "国内",
    "/sh/": "社会",
    "/cj/": "财经要闻",
    "/mil/": "国际",  # 军事归国际
}


def collect_chinanews():
    """中新网即时新闻 RSS：标题+简介+发布时间，按链接路径分类。"""
    resp = _get(CHINANEWS_RSS_URL)
    resp.encoding = "utf-8"
    root = ET.fromstring(resp.text)
    news = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip().strip("　 ")
        if not title:
            continue
        # 按链接路径确定分类，低价值频道（生活/健康/娱乐/体育）跳过
        cat = next(
            (c for path, c in _CHINANEWS_CAT_MAP.items() if path in link), None
        )
        if not cat:
            continue
        # pubDate: "Fri, 9 Oct 2026 08:19:06 +0800" → 时间戳
        ctime = ""
        pub = item.findtext("pubDate") or ""
        try:
            ctime = str(int(parsedate_to_datetime(pub).timestamp()))
        except Exception:
            pass
        news.append(
            {
                "title": title,
                "summary": desc,
                "source": "中新网",
                "category": cat,
                "ctime": ctime,
            }
        )
    return news


# ---------- 汇总 ----------

def collect_all():
    """依次采集所有启用的源，合并返回。单个源失败不影响整体。"""
    all_news = []
    # 排序即优先级：澎湃/中新网质量最高放前面；天行免费版常返回陈年缓存，降到最后备用
    collectors = [
        ("澎湃新闻", collect_thepaper),
        ("中新网", collect_chinanews),
        ("新浪新闻", collect_sina),
        ("微博热搜", collect_weibo_hot),
        ("百度热搜", collect_baidu_hot),
        ("联合早报", collect_zaobao),
        ("聚合数据", collect_juhe),
        ("天行数据", collect_tianapi),
    ]
    for name, func in collectors:
        try:
            items = func()
            print(f"[collect] {name}：{len(items)} 条")
            all_news.extend(items)
        except Exception as exc:
            print(f"[collect] {name} 采集失败：{exc}")
    return all_news


if __name__ == "__main__":
    print(f"采集时间：{datetime.now(BJT):%Y-%m-%d %H:%M:%S}")
    news = collect_all()
    print(f"\n合计 {len(news)} 条，按分类统计：")
    counts = {}
    for n in news:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    for k, v in counts.items():
        print(f"  {k}：{v} 条")
    print("\n前 15 条预览：")
    for n in news[:15]:
        print(f"  [{n['category']}] {n['title']}（{n['source']}）")
