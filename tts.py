"""语音版：把当日早报 markdown 合成 MP3（edge-tts，免费微软自然音色）。

给页面顶部的原生播放器用：微信内置浏览器、手机/电脑自带浏览器都能直接播放，
不用依赖浏览器的语音合成（那个在微信里根本用不了）。

设计要点：
- 只保留最新一期：覆盖写 docs/audio/latest.mp3，避免仓库无限膨胀
- 失败不抛异常：没装 edge-tts / 网络不通 / 文本异常 → 返回 (False, 原因)，
  页面自动不带播放器，推送照常
"""

import asyncio
import re
from pathlib import Path

from config import TTS_ENABLED, TTS_RATE, TTS_VOICE

BASE_DIR = Path(__file__).resolve().parent
AUDIO_REL = "docs/audio/latest.mp3"          # 相对仓库根目录（git/页面都用这个路径）
AUDIO_FILE = BASE_DIR / "docs" / "audio" / "latest.mp3"

_SECTION_RE = re.compile(r"^【(.+?)】$")
_ITEM_RE = re.compile(r"^\d+[.、]\s*(.*)$")


def build_script(markdown, title=""):
    """把早报 markdown 转成朗读稿（纯文本，无 markdown 标记，句间有停顿）。"""
    parts = []
    if title:
        parts.append(f"{title}。")
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        sec = _SECTION_RE.match(line)
        if sec:
            parts.append(f"{sec.group(1)}。")
            continue
        item = _ITEM_RE.match(line)
        if item:
            # 要点行：去掉序号与加粗标记，补句号让朗读有停顿
            head = item.group(1).replace("**", "").strip()
            parts.append(head.rstrip("。！？") + "。")
            continue
        parts.append(line.replace("**", "").strip())
    text = "".join(parts)
    text = re.sub(r"\s+", "", text)
    return text


async def _synthesize(text, out_path, voice, rate):
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def build_audio(markdown, title="", out_file=None):
    """合成 MP3（写到 docs/audio/latest.mp3）。返回 (ok, 说明)，不抛异常。"""
    if not TTS_ENABLED:
        return False, "语音版未启用（config.TTS_ENABLED=0）"
    text = build_script(markdown, title)
    if len(text) < 50:
        return False, "内容过短，跳过语音合成"
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False, "未安装 edge-tts（pip install edge-tts），本次不带语音版"

    out = Path(out_file or AUDIO_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        asyncio.run(_synthesize(text, tmp, TTS_VOICE, TTS_RATE))
    except Exception as exc:                     # 网络/接口变动都要能兜住
        tmp.unlink(missing_ok=True)
        return False, f"语音合成失败：{exc}"

    if not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        return False, "语音合成输出异常（文件为空）"

    tmp.replace(out)                             # 原子替换，避免半截文件被推送
    size_kb = out.stat().st_size // 1024
    return True, f"已生成 {AUDIO_REL}（{size_kb} KB，音色 {TTS_VOICE}，约 {len(text)} 字）"


if __name__ == "__main__":
    demo = "【国际】\n\n1. **示例要点**\n这是示例介绍。\n"
    print(build_script(demo, "每日新闻早报 示例")[:120])
