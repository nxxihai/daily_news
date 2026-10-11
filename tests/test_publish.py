"""网页版发布层回归测试：markdown 解析 / HTML 渲染 / 转义。

只测纯函数（不碰 git、不写文件），避免测试动到真实仓库。
"""

import unittest

from publish import parse_digest, render_html

SAMPLE = """【国际】

1. **中东局势出现新变化**
多国外长在日内瓦会面，会后发布联合声明。

2. **某国通过新法案**
【国内】

1. **多地启动防汛应急响应**
气象部门预计未来三天仍有强降雨，多地已转移群众。

【热点】

1. **多地景区迎来客流高峰**
"""


class ParseDigestTest(unittest.TestCase):
    def test_sections_and_counts(self):
        sections = parse_digest(SAMPLE)
        self.assertEqual([s["name"] for s in sections], ["国际", "国内", "热点"])
        self.assertEqual([len(s["items"]) for s in sections], [2, 1, 1])

    def test_head_and_desc_split(self):
        sections = parse_digest(SAMPLE)
        first = sections[0]["items"][0]
        self.assertEqual(first["head"], "中东局势出现新变化")
        self.assertEqual(first["desc"], "多国外长在日内瓦会面，会后发布联合声明。")
        # 没有介绍行的条目，desc 为空（只显示要点）
        self.assertEqual(sections[0]["items"][1]["desc"], "")

    def test_multiline_desc_joined(self):
        sections = parse_digest("【国内】\n\n1. **要点**\n第一行\n第二行\n")
        self.assertEqual(sections[0]["items"][0]["desc"], "第一行第二行")

    def test_unrecognized_line_falls_back_to_paragraph(self):
        sections = parse_digest("【国际】\n\n这是一句没有编号的说明。\n")
        item = sections[0]["items"][0]
        self.assertEqual(item["head"], "")
        self.assertEqual(item["desc"], "这是一句没有编号的说明。")

    def test_empty_markdown(self):
        self.assertEqual(parse_digest(""), [])


class RenderHtmlTest(unittest.TestCase):
    def test_sections_rendered_with_theme_color(self):
        page = render_html("每日新闻早报 2026-10-10", SAMPLE)
        self.assertIn('<h1 class="date">2026-10-10</h1>', page)
        self.assertIn("中东局势出现新变化", page)
        self.assertIn('<span class="count">2 条</span>', page)
        self.assertIn('style="--sec:#2563eb"', page)   # 国际
        self.assertIn('style="--sec:#dc2626"', page)   # 国内
        self.assertIn('style="--sec:#7c3aed"', page)   # 热点

    def test_html_escaped(self):
        page = render_html(
            "每日新闻早报 <b>2026-10-10</b>",
            "【国内】\n\n1. **<script>alert(1)</script>**\n介绍 & 内容\n",
        )
        # 新闻内容里的脚本必须被转义（页面自身的听新闻脚本是可信的）
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("介绍 &amp; 内容", page)

    def test_engine_note(self):
        """页面要标出用的是 AI 提炼还是规则精简版（排查云端 AI 通道是否可用）。"""
        self.assertIn("AI 提炼自公开新闻源", render_html("每日新闻早报 2026-10-11", SAMPLE))
        self.assertIn(
            "规则精简版（AI 通道不可用）",
            render_html("每日新闻早报 2026-10-11", SAMPLE, engine="rule"),
        )

    def test_empty_markdown_shows_notice(self):
        page = render_html("每日新闻早报", "")
        self.assertIn("本期内容尚未生成", page)

    def test_self_contained(self):
        """页面必须自包含：除内置的听新闻脚本外，不引用任何外部 css/js/图片。"""
        page = render_html("每日新闻早报 2026-10-10", SAMPLE)
        self.assertNotIn("<link ", page)
        self.assertIn("<style>", page)
        self.assertNotIn("src=", page)


class AudioPlayerTest(unittest.TestCase):
    """听新闻（MP3 原生播放器）：位置、破缓存、无音频时不渲染。"""

    def test_player_absent_without_audio(self):
        page = render_html("每日新闻早报 2026-10-11", SAMPLE)
        self.assertNotIn('class="player"', page)
        self.assertNotIn("<audio", page)

    def test_player_rendered_with_audio_and_sits_below_title(self):
        page = render_html(
            "每日新闻早报 2026-10-11", SAMPLE, audio_src="docs/audio/latest.mp3"
        )
        self.assertIn('class="player"', page)
        self.assertIn("<audio controls preload=", page)
        self.assertLess(page.index("</header>"), page.index('class="player"'))
        self.assertLess(page.index('class="player"'), page.index('<section class="sec"'))

    def test_audio_url_is_site_path_and_cache_busted(self):
        """页面里必须是站点路径 audio/latest.mp3（Pages 根=docs/），且带 ?v=日期 破缓存。"""
        page = render_html(
            "每日新闻早报 2026-10-11", SAMPLE, audio_src="docs/audio/latest.mp3"
        )
        self.assertIn('src="audio/latest.mp3?v=2026-10-11"', page)
        self.assertNotIn('src="docs/audio/', page)

    def test_page_is_script_free(self):
        """改回 MP3 方案后页面不再需要脚本，回归纯静态。"""
        page = render_html(
            "每日新闻早报 2026-10-11", SAMPLE, audio_src="docs/audio/latest.mp3"
        )
        self.assertNotIn("<script", page)
        self.assertIn("position:sticky", page)


if __name__ == "__main__":
    unittest.main()
