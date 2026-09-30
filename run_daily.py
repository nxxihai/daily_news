"""每日新闻早报主入口：采集 → 过滤 → 提炼 → 推送。

用法：
    python run_daily.py            完整跑一遍并推送微信
    python run_daily.py --dry-run  只采集+提炼，打印结果不推送（先用这个看效果）

可配合 Windows 任务计划程序，每天早上 8 点自动执行：
    操作：python  参数：run_daily.py 的完整路径
    起始于：本项目目录（daily_news）
"""

import os
import re
import socket
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import urllib3.util.connection

# 北京时间（UTC+8）：硬编码时区，避免系统时区设置不对导致日期错位
BJT = timezone(timedelta(hours=8))


def _force_ipv4():
    """强制所有网络请求走 IPv4。

    部分网络环境下 IPv6 半通：连接能建立但数据收发不到，
    会导致 requests 请求无限挂起（计划任务里曾因此卡死 3 分钟+）。
    """
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET


_force_ipv4()

# Windows 控制台用 UTF-8 输出，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from collectors import collect_all  # noqa: E402
from filter import (  # noqa: E402
    filter_by_date,
    filter_fresh,
    filter_quality,
    is_gossip,
    load_seen,
    mark_seen,
    save_seen,
)
from sender import send  # noqa: E402
from summarize import summarize  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SEEN_FILE = DATA_DIR / "seen.json"


def main(dry_run=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BJT).strftime("%Y-%m-%d")

    # 1. 采集
    news = collect_all()
    if not news:
        print("[run] 所有新闻源均无数据，终止。")
        return 1

    # 2. 过滤（黑名单 + 跨天去重）
    seen = load_seen(SEEN_FILE)
    n_raw = len(news)
    # 综合过滤：花边/游戏/个股期货研报
    news = filter_quality(news)
    n_clean = len(news)
    news = filter_by_date(news)
    n_date = len(news)
    fresh = filter_fresh(news, seen)
    print(
        f"[run] 采集 {n_raw} 条 → 过滤低质后 {n_clean} 条 → 过滤旧闻后 {n_date} 条 → 去重后 {len(fresh)} 条"
    )

    if not fresh:
        print("[run] 没有新新闻，今日跳过。")
        return 0

    # 3. 提炼
    title = f"每日新闻早报 {today}"
    markdown = summarize(fresh)

    if dry_run:
        print("=" * 56)
        print(title)
        print(markdown)
        print("=" * 56)
        print("[run] dry-run 模式，未推送。")
        return 0

    # 4. 推送（成功才记录去重，失败可安全重试）
    ok, msg = send(title, markdown)
    print(f"[run] {msg}")
    if ok:
        # 只记录实际推送出去的新闻标题，避免候选池被过度过滤
        pushed_titles = re.findall(r"\d+\.\s*\*\*(.+?)\*\*", markdown)
        seen = mark_seen(seen, pushed_titles)
        save_seen(SEEN_FILE, seen)
        print(f"[run] 已记录 {len(pushed_titles)} 条推送标题，供明日去重。")
        return 0
    print("[run] 推送失败，未记录去重，可直接重跑。")
    return 1


def _thread_main(dry_run):
    code = main(dry_run=dry_run)
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    # 总超时护栏：网络异常时最多跑 300 秒，避免计划任务永久挂起
    runner = threading.Thread(target=_thread_main, args=(dry_run,), daemon=True)
    runner.start()
    runner.join(timeout=300)
    if runner.is_alive():
        print("[run] 执行超过 300 秒，强制结束（常见于网络请求挂起）。")
        os._exit(2)

