"""语音版（听新闻 MP3）回归测试：朗读稿生成、后端回退与重试、失败兜底。

不联网：后端调用被 mock，只验证编排逻辑（顺序、重试次数、降级、失败信息）。
"""

import tempfile
import unittest
from pathlib import Path

import tts
from tts import build_audio, build_script

MARKDOWN = """【国际】

1. **美国宣布制裁国际刑事法院**
美方7日宣布对国际刑事法院两名法官实施制裁。

2. **巴拿马发生7.6级强震**
【国内】

1. **中法举行第二十八次战略对话**
王毅10日在同法国总统外事顾问博纳举行会谈。
"""


class BuildScriptTest(unittest.TestCase):
    def test_sections_and_items_in_order(self):
        text = build_script(MARKDOWN, "每日新闻早报 2026-10-11")
        self.assertIn("每日新闻早报2026-10-11", text)   # 朗读稿不留空格
        self.assertLess(text.index("国际。"), text.index("美国宣布制裁国际刑事法院"))
        self.assertLess(text.index("国内。"), text.index("中法举行第二十八次战略对话"))

    def test_markdown_markers_stripped(self):
        text = build_script(MARKDOWN)
        self.assertNotIn("**", text)
        self.assertNotIn("【", text)
        self.assertIn("国际。", text)

    def test_no_whitespace_left(self):
        self.assertNotIn("\n", build_script(MARKDOWN))
        self.assertNotIn(" ", build_script(MARKDOWN))


class BackendFallbackTest(unittest.TestCase):
    """后端顺序 + 每后端重试次数 + 全失败兜底。"""

    def _run(self, plan, backends=("edge",)):
        """plan: {后端名: [(ok, 说明), ...]}，每个后端按自己的序列消费。

        返回 (ok, msg, 调用序列)。
        """
        calls = []

        def fake_run_backend(name, text, out_path):
            calls.append(name)
            seq = plan.get(name) or [(False, f"{name} 失败")]
            idx = calls.count(name) - 1
            ok, detail = seq[min(idx, len(seq) - 1)]
            if ok:
                out_path.write_bytes(b"x" * 2048)
            return ok, detail

        orig_backend = tts._run_backend
        orig_backends = tts._available_backends
        orig_delay = tts.TTS_RETRY_DELAY
        tts._run_backend = fake_run_backend
        tts._available_backends = lambda: list(backends)
        tts.TTS_RETRY_DELAY = 0
        out = Path(tempfile.gettempdir()) / "_tts_test_output.mp3"
        try:
            ok, msg = build_audio(MARKDOWN, "每日新闻早报", out_file=out)
        finally:
            tts._run_backend = orig_backend
            tts._available_backends = orig_backends
            tts.TTS_RETRY_DELAY = orig_delay
            out.unlink(missing_ok=True)
        return ok, msg, calls

    def test_first_backend_success_no_retry(self):
        ok, msg, calls = self._run({"edge": [(True, "edge")]})
        self.assertTrue(ok)
        self.assertEqual(calls, ["edge"])
        self.assertIn("edge 后端", msg)

    def test_retry_then_fallback_to_azure(self):
        """edge 连失 3 次 → 自动切到 Azure 并成功。"""
        ok, msg, calls = self._run(
            {"edge": [(False, "edge 失败：403")], "azure": [(True, "azure")]},
            backends=("edge", "azure"),
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["edge"] * tts.TTS_ATTEMPTS + ["azure"])
        self.assertIn("azure 后端", msg)

    def test_all_backends_fail_returns_reason(self):
        ok, msg, calls = self._run(
            {"edge": [(False, "edge 失败：403")], "azure": [(False, "azure 失败：401")]},
            backends=("edge", "azure"),
        )
        self.assertFalse(ok)
        self.assertEqual(calls, ["edge"] * tts.TTS_ATTEMPTS + ["azure"] * tts.TTS_ATTEMPTS)
        self.assertIn("403", msg)
        self.assertIn("401", msg)

    def test_too_short_skipped(self):
        ok, msg = build_audio("【国际】\n\n1. **短**\n", "每日新闻早报")
        self.assertFalse(ok)
        self.assertIn("过短", msg)


class AvailableBackendsTest(unittest.TestCase):
    def test_edge_first_azure_only_if_key_present(self):
        backends = tts._available_backends()
        self.assertEqual(backends[0], "edge")           # edge-tts 已安装
        if not (tts.AZURE_SPEECH_KEY and tts.AZURE_SPEECH_REGION):
            self.assertNotIn("azure", backends)          # 没配 key 就不启用官方后端


if __name__ == "__main__":
    unittest.main()