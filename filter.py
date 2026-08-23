"""过滤层：花边新闻黑名单 + 标题去重（跨天）。

设计要点：
- seen 记录「已成功推送」的标题哈希，而不是「已采集」的，
  这样推送失败重跑时不会把新闻误判为已推送。
- 保存时只保留最近 keep_days 天的记录，防止历史无限增长。
"""

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# 花边 / 低信息量关键词黑名单（命中标题即过滤）
GOSSIP_KEYWORDS = [
    "明星", "绯闻", "恋情", "分手", "出轨", "八卦", "吃瓜", "网红",
    "综艺", "选秀", "剧组", "票房", "番剧", "演唱会", "应援", "粉丝",
    "塌房", "官宣", "路透", "生图", "剧透", "爆料", "开撕", "互撕",
    "炒作", "蹭热度", "微博之夜", "跨年晚会", "红毯", "带货", "站姐",
    "婚变", "情变", "小三", "傍大款", "整容", "离婚协议", "被曝",
]

SEEN_KEEP_DAYS = 30  # 历史去重记录保留天数


def normalize_title(title):
    """去掉标点、空白后归一化，避免同一新闻因标点差异判重失败。"""
    return re.sub(r"[\W_]+", "", title).lower()[:30]


def title_hash(title):
    """生成标题哈希，用于跨天去重。"""
    return hashlib.md5(normalize_title(title).encode("utf-8")).hexdigest()


def is_gossip(title):
    """是否属于花边/低信息量新闻。"""
    return any(kw in title for kw in GOSSIP_KEYWORDS)


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
