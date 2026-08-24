"""多源新闻采集：百度热搜（免key）/ 天行数据 / 聚合数据。

每个采集器独立，失败只影响自身，collect_all() 汇总所有可用源的数据。
返回的新闻统一为字典：
    {"title": 标题, "summary": 摘要/描述, "source": 来源, "category": 分类}
分类取值：国际 / 国内 / 社会 / 财经 / 科技 / 军事 / 热点
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
    """抓取百度实时热搜榜，全部标为「热点」。"""
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


# ---------- 新浪滚动新闻（免 key，国际/科技/财经） ----------

SINA_ROLL_URL = "https://feed.mix.sina.com.cn/api/roll/get"
SINA_HEADERS = {"User-Agent": UA, "Referer": "https://news.sina.com.cn/"}

# 标题中出现这些词 → 国际要闻；否则归国内。新浪 lid 分类不纯，用关键词粗分
INTERNATIONAL_KEYWORDS = [
    "美国", "英国", "法国", "德国", "俄罗斯", "日本", "韩国", "朝鲜",
    "欧盟", "北约", "伊朗", "以色列", "乌克兰", "印度", "澳大利亚",
    "加拿大", "土耳其", "沙特", "泰国", "越南", "新加坡", "巴西", "墨西哥",
    "特朗普", "普京", "马克龙", "泽连斯基", "内塔尼亚胡", "联合国", "世卫",
    "美元", "欧元", "日元", "全球", "国际", "白宫", "克里姆林宫", "五角大楼",
    "英伟达", "特斯拉", "苹果", "谷歌", "微软", "OpenAI", "Anthropic",
    "三星", "SK海力士", "台积电", "ASML", "欧洲", "中东", "非洲", "亚洲",
]


def _sina_category(title, default):
    if any(kw in title for kw in INTERNATIONAL_KEYWORDS):
        return "国际要闻"
    return default


def collect_sina():
    """新浪滚动新闻接口（免 key）：2516 国际要闻 / 2515 科技 / 2509 财经要闻。"""
    import random

    news = []
    channels = [("2516", "国际要闻"), ("2515", "科技"), ("2509", "财经要闻")]
    for lid, default_cat in channels:
        try:
            resp = requests.get(
                SINA_ROLL_URL,
                params={
                    "pageid": "153",
                    "lid": lid,
                    "k": "",
                    "num": "15",
                    "page": "1",
                    "_": str(random.randint(10**13, 10**14)),  # 随机参数绕过接口缓存
                },
                headers=SINA_HEADERS,
                timeout=12,
            )
            data = resp.json().get("result", {}).get("data", [])
            for item in data:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                news.append(
                    {
                        "title": title,
                        "summary": str(item.get("intro", "") or "").strip(),
                        "source": "新浪新闻",
                        "category": _sina_category(title, default_cat),
                    }
                )
        except Exception as exc:
            print(f"[sina] lid {lid} 采集失败：{exc}")
    return news


# ---------- 天行数据（需 key，通用新闻接口 generalnews） ----------
# 接口文档：https://www.tianapi.com/apiview/235  （综合新闻查询）
# 一次拉取最新资讯，返回数据无分类字段，用标题关键词粗分国际/国内
TIANAPI_NEWS_URL = "https://apis.tianapi.com/generalnews/index"
CATEGORY_CN = {
    "guoji": "国际",
    "guonei": "国内",
    "shehui": "社会",
    "caijing": "财经",
    "keji": "科技",
    "junshi": "军事",
}


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
                    "category": (
                        "国际" if any(kw in title for kw in INTERNATIONAL_KEYWORDS) else "国内"
                    ),
                }
            )
    except Exception as exc:
        print(f"[tianapi] generalnews 采集失败：{exc}")
    return news


# ---------- 聚合数据（需 key，分类 type 查询） ----------
# 使用「AI新闻简报」接口（ID: 850），文档：https://apis.juhe.cn/fapigw/aibrief/list
JUHE_NEWS_URL = "https://apis.juhe.cn/fapigw/aibrief/list"
# type 映射：AI新闻简报的 type 值 → 早报分类
JUHE_TYPE_MAP = {
    "hot": "热点",
    "tech": "科技",
    "money": "财经",
    "war": "军事",
    "sports": "体育",
    "ent": "娱乐",
    "auto": "汽车",
    "game": "游戏",
    "mobile": "科技",
    "digi": "科技",
}


def collect_juhe():
    """聚合数据 AI 新闻简报接口，按分类各取若干条。

    接口返回结构：result.list（新闻数组），每条含 title / summary / type 等字段。
    每个分类单独请求，超时 8 秒，失败不影响其他分类。
    """
    if not JUHE_KEY:
        print("[juhe] 未配置 JUHE_KEY，跳过聚合数据（https://www.juhe.cn/ 注册后获取）")
        return []
    news = []
    # 优先采集热点/财经/科技/军事，其他作为补充；减少分类数避免超时
    types_to_fetch = ["hot", "money", "tech", "war", "sports"]
    for type_ in types_to_fetch:
        try:
            # 单独设置短超时，避免单个分类卡死拖垮整体
            resp = _get(
                JUHE_NEWS_URL,
                params={"key": JUHE_KEY, "type": type_, "page_size": 10},
                timeout=8,
            )
            data = resp.json()
            if data.get("error_code") != 0:
                print(f"[juhe] 分类 {type_} 返回错误：{data.get('reason')}")
                continue
            result = data.get("result") or {}
            items = result.get("list") or []
            print(f"[juhe] 分类 {type_} 采到 {len(items)} 条")
            for item in items:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                # AI新闻简报字段名：summary（摘要）、author_name（来源）
                summary = str(item.get("summary", "") or "").strip()
                raw_type = str(item.get("type", "")).strip()
                # 映射到早报分类：国际/国内/热点；未知类型按 JUHE_TYPE_MAP 或归国内
                cat = JUHE_TYPE_MAP.get(raw_type, "国内")
                if cat in ("军事",):
                    cat = "国际"
                elif cat in ("科技", "财经", "体育", "娱乐", "汽车", "游戏"):
                    cat = "国内"
                news.append(
                    {
                        "title": title,
                        "summary": summary,
                        "source": str(item.get("author_name", "聚合数据") or "聚合数据").strip(),
                        "category": cat,
                    }
                )
        except Exception as exc:
            print(f"[juhe] 分类 {type_} 采集失败：{exc}")
    return news


# ---------- 汇总 ----------

def collect_all():
    """依次采集所有启用的源，合并返回。单个源失败不影响整体。"""
    all_news = []
    collectors = [
        ("聚合数据", collect_juhe),
        ("百度热搜", collect_baidu_hot),
        ("新浪新闻", collect_sina),
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
    print(f"采集时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    news = collect_all()
    print(f"\n合计 {len(news)} 条，按分类统计：")
    counts = {}
    for n in news:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    for k, v in counts.items():
        print(f"  {k}：{v} 条")
    print("\n前 10 条预览：")
    for n in news[:10]:
        print(f"  [{n['category']}] {n['title']}（{n['source']}）")
