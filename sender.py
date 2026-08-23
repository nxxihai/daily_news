"""微信推送：Server酱（主通道）+ PushPlus（备用通道）。

send() 会先走 Server酱，失败自动切 PushPlus，两边都失败才算失败。
"""

import requests

from config import PUSHPLUS_TOKEN, SERVERCHAN_KEY


def send_serverchan(title, markdown):
    """Server酱 Turbo 版推送。成功返回 (True, 信息)。"""
    if not SERVERCHAN_KEY:
        return False, "未配置 Server酱 SendKey"
    try:
        resp = requests.get(
            f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send",
            params={"title": title, "desp": markdown},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            return True, "Server酱 发送成功"
        return False, f"Server酱 返回错误：{data}"
    except Exception as exc:
        return False, f"Server酱 请求失败：{exc}"


def send_pushplus(title, markdown):
    """PushPlus 推送（markdown 模板）。成功返回 (True, 信息)。"""
    if not PUSHPLUS_TOKEN:
        return False, "未配置 PushPlus token"
    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": markdown,
                "template": "markdown",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 200:
            return True, "PushPlus 发送成功"
        return False, f"PushPlus 返回错误：{data}"
    except Exception as exc:
        return False, f"PushPlus 请求失败：{exc}"


def send(title, markdown):
    """双通道推送：主 Server酱，失败切 PushPlus。"""
    ok, msg = send_serverchan(title, markdown)
    if ok:
        return True, msg
    print(f"[sender] {msg}，尝试 PushPlus…")
    return send_pushplus(title, markdown)


if __name__ == "__main__":
    import sys

    title = "测试：每日新闻早报"
    body = "# 测试\n\n这是一条测试消息，用于验证双通道推送。"
    ok, msg = send(title, body)
    print(msg)
    sys.exit(0 if ok else 1)
