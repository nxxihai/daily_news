"""语音版：把当日早报 markdown 合成 MP3，供页面原生播放器播放。

两个后端，按顺序尝试（都不依赖浏览器语音合成，所以微信里也能播）：
1. edge-tts：免费、不用 key，但它走的是微软 Edge 的**非官方免费端点**——
   微软改鉴权就会直接 403（2026-10-13 实测），等库更新才能恢复
2. Azure Speech（官方 REST）：稳定、需在环境变量里给 key+region，
   免费额度 50 万字符/月（每天约 800 字，够用一年多）

约定：
- 只保留最新一期：覆盖写 docs/audio/latest.mp3，避免仓库无限膨胀
- 失败不抛异常：返回 (False, 原因)，页面自动不带播放器，推送照常
- 失败原因会被写进页面提交信息（git log 就能看到，不用翻 Actions 日志）
"""

import asyncio
import re
import time
import xml.sax.saxutils as saxutils
from pathlib import Path

from config import (AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, TTS_ATTEMPTS,
                    TTS_RATE, TTS_RETRY_DELAY, TTS_VOICE)

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


# ================= 后端 1：edge-tts（免费端点，随时可能被微软改坏） =================
async def _edge_synthesize(text, out_path):
    import edge_tts

    await edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE).save(str(out_path))


# ================= 后端 2：Azure Speech 官方 REST（需 key，稳定） =================
AZURE_ENDPOINT = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
AZURE_OUTPUT = "audio-24khz-48kbitrate-mono-mp3"


def _azure_synthesize(text, out_path):
    import requests

    ssml = (
        "<speak version='1.0' xml:lang='zh-CN'>"
        f"<voice name='{TTS_VOICE}'>"
        f"<prosody rate='{TTS_RATE}'>{saxutils.escape(text)}</prosody>"
        "</voice></speak>"
    )
    resp = requests.post(
        AZURE_ENDPOINT.format(region=AZURE_SPEECH_REGION),
        data=ssml.encode("utf-8"),
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": AZURE_OUTPUT,
            "User-Agent": "daily-news-tts",
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Azure HTTP {resp.status_code}: {resp.text[:120]}")
    out_path.write_bytes(resp.content)


def _run_backend(name, text, out_path):
    """跑单个后端：edge / azure。返回 (ok, 说明)。"""
    tmp = out_path.with_name(out_path.name + f".{name}.tmp")
    try:
        if name == "azure":
            _azure_synthesize(text, tmp)
        else:
            asyncio.run(_edge_synthesize(text, tmp))
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return False, f"{name} 失败：{str(exc).replace(chr(10), ' ')[:110]}"
    if not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        return False, f"{name} 输出异常（空文件）"
    tmp.replace(out_path)
    return True, name


def _available_backends():
    """当前可用的语音后端顺序：edge-tts（免费）优先，Azure 官方（有 key 才启用）。"""
    backends = []
    try:
        import edge_tts  # noqa: F401
        backends.append("edge")
    except ImportError:
        pass
    if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        backends.append("azure")
    return backends


def build_audio(markdown, title="", out_file=None):
    """合成 MP3（写到 docs/audio/latest.mp3）。返回 (ok, 说明)，不抛异常。

    后端按可用性依次尝试：先 edge-tts（免费），失败再 Azure（需 key）；
    每个后端内部还会重试 TTS_ATTEMPTS 次，应付偶发网络抖动。
    """
    text = build_script(markdown, title)
    if len(text) < 50:
        return False, "内容过短，跳过语音合成"

    out = Path(out_file or AUDIO_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)

    backends = _available_backends()
    if not backends:
        return False, "没有可用语音后端（edge-tts 未安装、Azure key 未配置）"

    problems = {}        # 每个后端只记第一次失败原因，提交信息里更短更干净
    for name in backends:
        for attempt in range(1, TTS_ATTEMPTS + 1):
            ok, detail = _run_backend(name, text, out)
            if ok:
                kb = out.stat().st_size // 1024
                extra = f"，第 {attempt}/{TTS_ATTEMPTS} 次尝试成功" if attempt > 1 else ""
                return True, f"已生成 {AUDIO_REL}（{kb} KB，{name} 后端，音色 {TTS_VOICE}{extra}）"
            problems.setdefault(name, detail)
            if attempt < TTS_ATTEMPTS:
                time.sleep(TTS_RETRY_DELAY)
    return False, "；".join(problems.values()) or "语音合成失败"


if __name__ == "__main__":
    demo = "【国际】\n\n1. **示例要点**\n这是示例介绍。\n"
    print(build_script(demo, "每日新闻早报 示例")[:120])