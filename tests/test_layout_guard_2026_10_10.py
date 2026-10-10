"""2026-10-10 早报观感问题的回归用例。

当天推送被指"有些错乱"，逐条落地为测试：
- 分类错位：伊朗/巴拿马等国际硬新闻被塞进热点栏、外国议员新闻被塞进国内栏
- 同主线重复：美俄柴油协议 vs 俄将向美供应石油；中欧贸易战 vs 欧盟谅解
- 企业公告：上市公司人事处罚公告（双汇/未领取薪酬）不该进早报
- 超长要点：把官方措辞整句抄进要点，页面上又长又乱
- 页面排版：有介绍的条目应排在前面，仅要点条目用 brief 样式
"""

import unittest

from publish import render_html
from summarize import (
    _classify_by_title,
    _is_same_event,
    _post_filter_sections,
    _shorten_head,
    sections_to_markdown,
)


class ClassifyTest(unittest.TestCase):
    def test_foreign_hard_news_is_international(self):
        """命中"违规"这类通用社会词，但仍属国际要闻。"""
        self.assertEqual(
            _classify_by_title("伊朗革命卫队：将不限于在霍尔木兹海峡打击违规船只"),
            "国际",
        )

    def test_foreign_disaster_is_international(self):
        self.assertEqual(
            _classify_by_title("巴拿马发生70多年来最强地震 总统称损失严重"), "国际"
        )

    def test_foreign_lawmaker_is_international(self):
        self.assertEqual(
            _classify_by_title("日议员古屋圭司：高市早苗称我遭中国制裁是“荣誉”"),
            "国际",
        )

    def test_foreign_controversy_is_international(self):
        """热点侧命中"争议+引发"两个通用词，仍应判国际。"""
        self.assertEqual(
            _classify_by_title("美国直播枪决计划引发跨党派强烈争议"), "国际"
        )

    def test_domestic_accident_stays_hotspot(self):
        self.assertEqual(
            _classify_by_title("湖北烟花店爆燃12人死亡 调查报告指顾客违规试放引发"),
            "热点",
        )

    def test_foreign_social_oddity_stays_hotspot(self):
        """带国名的社会奇闻（热点词不是通用词）不该被挪进国际栏。"""
        self.assertEqual(
            _classify_by_title("美国一男子婚礼上新郎被自家狗咬伤"), "热点"
        )


class SameMainlineTest(unittest.TestCase):
    def test_abbreviation_expanded(self):
        """"美俄"是简称、"俄将向美…"只剩单字，都要能认出同一主线。"""
        self.assertTrue(_is_same_event(
            "美俄达成柴油协议", "乌总统对美俄达成柴油协议表示失望。",
            "俄将向美及全球供应石油", "普京确认俄将向美国和全球市场提供石油和石油产品。",
        ))

    def test_china_eu_trade_mainline(self):
        self.assertTrue(_is_same_event(
            "中欧避免全面贸易战", "中欧就汽车稀土商品关税出现谈判突破。",
            "欧盟称与中国达成谅解 中国混动车对欧出口有望减半", "",
        ))

    def test_unrelated_us_news_kept(self):
        """只有共同国名、主题不同，不能误合并。"""
        self.assertFalse(_is_same_event(
            "美国宣布对中国商品加征关税", "",
            "美国一客机因故障紧急迫降", "",
        ))


class CompanyNoticeTest(unittest.TestCase):
    def test_company_notice_filtered(self):
        sections = {
            "国内": [
                ("万宏伟被罚3764万", "双汇发展公告，万宏伟被罚3764万，未从公司领取薪酬。"),
                ("重庆贵州湖南北部等地降水偏多", "中央气象台发布中期天气预报，多地降水持续偏多。"),
            ]
        }
        out = _post_filter_sections(sections)
        titles = [t for t, _ in out.get("国内", [])]
        self.assertNotIn("万宏伟被罚3764万", titles)
        self.assertIn("重庆贵州湖南北部等地降水偏多", titles)


class HeadShortenTest(unittest.TestCase):
    LONG = ("东方甄选回应青虾仁净含量偏差争议："
            "自然解冻的称重方法与现行法规要求的计量检测标准不一致")

    def test_long_head_shortened(self):
        self.assertEqual(_shorten_head(self.LONG), "东方甄选回应青虾仁净含量偏差争议")

    def test_normal_head_kept(self):
        self.assertEqual(
            _shorten_head("警方通报王皓被围堵辱骂处理结果"),
            "警方通报王皓被围堵辱骂处理结果",
        )

    def test_markdown_uses_shortened_head(self):
        md = sections_to_markdown({"热点": [(self.LONG, "公司表示将按标准复检并公布结果。")]})
        bullet = next(ln for ln in md.splitlines() if ln.startswith("1."))
        self.assertEqual(bullet, "1. **东方甄选回应青虾仁净含量偏差争议**")


class PageLayoutTest(unittest.TestCase):
    MD = ("【热点】\n\n"
          "1. **只有要点的简讯**\n\n"
          "2. **带介绍的要点**\n这是一句介绍，说明具体事实。\n")

    def test_items_with_desc_come_first(self):
        page = render_html("每日新闻早报 2026-10-10", self.MD)
        self.assertLess(page.index("带介绍的要点"), page.index("只有要点的简讯"))

    def test_brief_style_applied(self):
        page = render_html("每日新闻早报 2026-10-10", self.MD)
        self.assertIn('class="item brief"', page)


if __name__ == "__main__":
    unittest.main()
