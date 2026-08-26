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
from datetime import datetime

import requests

from config import JUHE_KEY, TIANAPI_KEY

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _get(url, params=None, timeout=12):
    resp = requests.get(
        url, params=params, headers={"User-Agent": UA}, timeout=timeout
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

    - 国际关键词 → 国际要闻
    - 社会新闻关键词 → 社会（归入热点栏）
    - 其他保留源分类
    """
    # 先检查是否真的是国际要闻（排除外国公司名干扰）
    intl_hits = sum(1 for kw in INTERNATIONAL_KEYWORDS if kw in title)
    if intl_hits >= 1:
        # 国际关键词命中，归国际要闻
        # 注意：如"中国回应美国制裁"这类新闻虽然涉及中国，但核心是国际关系
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
            resp = requests.get(
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
                news.append(
                    {
                        "title": title,
                        "summary": str(item.get("intro", "") or "").strip(),
                        "source": "新浪新闻",
                        "category": cat,
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
                }
            )
    except Exception as exc:
        print(f"[tianapi] generalnews 采集失败：{exc}")
    return news


# ---------- 聚合数据（需 key，AI新闻简报） ----------
# 接口文档：https://apis.juhe.cn/fapigw/aibrief/list
JUHE_NEWS_URL = "https://apis.juhe.cn/fapigw/aibrief/list"
# type 映射：AI新闻简报的 type 值 → 早报分类或"_skip"（直接跳过）
JUHE_TYPE_MAP = {
    "hot": "热点",
    "tech": "科技",
    "money": "财经",
    "war": "军事",
    "sports": "_skip",    # 体育新闻降权跳过
    "ent": "_skip",       # 娱乐新闻直接跳过
    "auto": "科技",       # 汽车归入科技
    "game": "_skip",      # 游戏新闻直接跳过
    "mobile": "科技",
    "digi": "科技",
    "world": "国际",      # 如果有的话
    "society": "社会",    # 社会新闻
}


def collect_juhe():
    """聚合数据 AI 新闻简报接口，只请求 1 次取全部新闻（节省免费版额度）。"""
    if not JUHE_KEY:
        print("[juhe] 未配置 JUHE_KEY，跳过聚合数据（https://www.juhe.cn/ 注册后获取）")
        return []
    news = []
    try:
        resp = _get(
            JUHE_NEWS_URL,
            params={"key": JUHE_KEY, "page_size": 20},
            timeout=10,
        )
        data = resp.json()
        if data.get("error_code") != 0:
            print(f"[juhe] 返回错误：{data.get('reason')}")
            return news
        result = data.get("result") or {}
        items = result.get("list") or []
        print(f"[juhe] 采到 {len(items)} 条")
        for item in items:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            summary = str(item.get("summary", "") or "").strip()
            raw_type = str(item.get("type", "")).strip()
            cat = JUHE_TYPE_MAP.get(raw_type, "国内")
            # 跳过的分类
            if cat == "_skip":
                continue
            # 军事 → 国际
            if cat == "军事":
                cat = "国际"
            news.append(
                {
                    "title": title,
                    "summary": summary,
                    "source": str(item.get("author_name", "聚合数据") or "聚合数据").strip(),
                    "category": cat,
                }
            )
    except Exception as exc:
        print(f"[juhe] 采集失败：{exc}")
    return news


# ---------- 汇总 ----------

def collect_all():
    """依次采集所有启用的源，合并返回。单个源失败不影响整体。"""
    all_news = []
    collectors = [
        ("新浪新闻", collect_sina),
        ("百度热搜", collect_baidu_hot),
        ("天行数据", collect_tianapi),
        ("聚合数据", collect_juhe),
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
    print(f"采集时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
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
