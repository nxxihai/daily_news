"""语音版（听新闻 MP3）回归测试：朗读稿生成 + 失败兜底。

不联网：只测纯函数（合成 MP3 那步由本地/云端实跑验证）。
"""

import unittest

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
        self.assertLess(text.index("国际。"), text.index("国内。"))

    def test_markdown_markers_stripped(self):
        text = build_script(MARKDOWN)
        self.assertNotIn("**", text)
        self.assertNotIn("【", text)
        self.assertNotIn("1. ", text)
        # 类名保留（作为朗读时的分栏提示）
        self.assertIn("国际。", text)

    def test_no_whitespace_left(self):
        self.assertNotIn("\n", build_script(MARKDOWN))
        self.assertNotIn(" ", build_script(MARKDOWN))


class BuildAudioFallbackTest(unittest.TestCase):
    def test_disabled_returns_reason(self):
        original = tts.TTS_ENABLED
        tts.TTS_ENABLED = False
        try:
            ok, msg = build_audio(MARKDOWN, "每日新闻早报")
        finally:
            tts.TTS_ENABLED = original
        self.assertFalse(ok)
        self.assertIn("未启用", msg)

    def test_too_short_skipped(self):
        ok, msg = build_audio("【国际】\n\n1. **短**\n", "每日新闻早报")
        self.assertFalse(ok)
        self.assertIn("过短", msg)


if __name__ == "__main__":
    unittest.main()
