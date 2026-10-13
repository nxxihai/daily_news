"""2026-10-13 早报内容问题的回归用例。

当天暴露的问题：
- 国内栏只剩 4 条且塞进"房产成交/评论稿/辟谣"——国内候选池太薄，民生类没归热点
- 评论稿"来论｜…对中国的宏观政策有何启示？"没被拦
- 外国科技公司动态"微软准备…部署自研芯片 规模达数吉瓦"没被拦
- 标题"喀纳斯棕熊索食险酿大祸？新疆布尔津县委网信办：网传内容系AI合成"
  被截断成悬空疑问句
"""

import unittest

from summarize import (  # noqa: E402
    _POST_FILTER_BAN,
    _classify_by_title,
    _post_filter_sections,
    _shorten_head,
)


class HeadTruncationTest(unittest.TestCase):
    def test_question_mark_head_keeps_full_title(self):
        """冒号前是疑问句时不能截，否则信息全丢。"""
        title = "喀纳斯棕熊索食险酿大祸？新疆布尔津县委网信办：网传内容系AI合成"
        self.assertEqual(_shorten_head(title), title)

    def test_long_but_plain_head_still_truncated(self):
        """冒号前是长陈述句时仍可压缩（保持之前的降噪行为）。"""
        out = _shorten_head("东方甄选回应青虾仁净含量偏差争议：自然解冻的称重方法与现行法规要求不一致")
        self.assertNotIn("：", out)
        self.assertLessEqual(len(out), 30)


class CommentDraftTest(unittest.TestCase):
    def test_pure_question_headline_dropped(self):
        """纯疑问句标题（无任何数字）是观点/评论稿，不该进早报。"""
        sections = {
            "国内": [("来论｜扩消费、稳就业，今年诺贝尔经济学奖对中国宏观政策有何启示？", "")]
        }
        self.assertEqual(_post_filter_sections(sections)["国内"], [])

    def test_question_with_number_kept(self):
        """疑问句但带具体数字的，仍是事实性提问，保留。"""
        sections = {"国内": [("某地明日气温将达多少度？12日最高达38℃",
                              "气象台预报，明日最高气温将达38℃，局部地区有雷阵雨。")]}
        self.assertEqual(len(_post_filter_sections(sections)["国内"]), 1)


class BanListTest(unittest.TestCase):
    def test_comment_prefixes_banned(self):
        for ban in ("来论｜", "｜评论", "专访：", "访谈："):
            self.assertIn(ban, _POST_FILTER_BAN)

    def test_foreign_bigtech_banned(self):
        for ban in ("微软", "谷歌", "数据中心", "自研芯片", "吉瓦"):
            self.assertIn(ban, _POST_FILTER_BAN)


class ClassifyTest(unittest.TestCase):
    def test_housing_market_goes_hotspot(self):
        """房产成交属民生/城市生活，归热点，把国内栏留给政策与外交。"""
        self.assertEqual(_classify_by_title("上海二手房成交破1600套"), "热点")

    def test_policy_stays_domestic(self):
        self.assertEqual(
            _classify_by_title("中国官方“数据找人”鼓励参保 分析指现行制度与灵活就业现实不匹配"),
            "国内",
        )


if __name__ == "__main__":
    unittest.main()