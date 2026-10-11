"""2026-10-11 早报质量问题的回归用例（当天云端没有 AI key，走了规则兜底）。

暴露的问题：
- 源电头/记者署名/口令碎片原样进早报（"中新网衡水10月10日电 (邢璐 王鹏 张凯)"、"5、4、3、2、1，点火！"）
- 摘要与要点串台（"白宫新任发言人"配了"佩斯科夫表态俄罗斯"的简介）
- 标题复述当介绍（"美国制裁国际刑事法院" → "美国对国际刑事法院实施制裁，可能严重制约其运作能力。"）
- 分类错位（"人物｜白宫新任发言人：曾在中国生活三年"掉进国内栏）
- 栏目前缀 / 截断尾段（"人物｜…"、"…，何小鹏"、"…，家属代理律师"）
"""

import unittest

from summarize import (
    _borrow_ok,
    _classify_by_title,
    _is_title_repeat,
    _one_line_summary,
    _rich_summary,
    _shorten_head,
    _strip_source_noise,
    engine_used,
    normalize_title,
)


class SourceNoiseTest(unittest.TestCase):
    def test_byline_stripped(self):
        raw = "中新网衡水10月10日电 (邢璐 王鹏 张凯)“5、4、3、2、1，点火！"
        self.assertEqual(_strip_source_noise(raw), "“5、4、3、2、1，点火！")

    def test_reporter_clause_stripped(self):
        raw = "记者今天了解到，市场监管总局研究起草了《食品安全法（修订草案征求意见稿）》。"
        out = _strip_source_noise(raw)
        self.assertFalse(out.startswith("记者"))
        self.assertTrue(out.startswith("市场监管总局"))

    def test_countdown_fragment_rejected(self):
        """只剩口令碎片的源简介不能当介绍用。"""
        n = {
            "title": "河北冀州学生制作90枚模型火箭致敬长征胜利90周年",
            "summary": "中新网衡水10月10日电 (邢璐 王鹏 张凯)“5、4、3、2、1，点火！",
        }
        self.assertIsNone(_rich_summary(n))

    def test_real_summary_after_noise_still_usable(self):
        n = {
            "title": "食品安全法修订公开征求意见",
            "summary": "记者今天了解到，市场监管总局研究起草了《食品安全法（修订草案征求意见稿）》，"
                       "并向社会公开征求意见。",
        }
        out = _rich_summary(n)
        self.assertIsNotNone(out)
        self.assertNotIn("记者今天了解到", out)


class BorrowGuardTest(unittest.TestCase):
    def test_unrelated_borrow_rejected(self):
        """白宫发言人那条不能借用佩斯科夫表态的简介。"""
        a = normalize_title("人物｜白宫新任发言人：曾在中国生活三年，上周还不在候选名单上")
        b = normalize_title(
            "10月10日，俄罗斯总统新闻秘书佩斯科夫表示，普京已向美国总统特朗普转达伊朗构想"
        )
        self.assertFalse(_borrow_ok(a, b))

    def test_same_event_borrow_allowed(self):
        a = normalize_title("冷藏车违规载人致8人窒息身亡")
        b = normalize_title("河南平顶山冷藏车违规载人事故8人遇难")
        self.assertTrue(_borrow_ok(a, b))


class TitleRepeatTest(unittest.TestCase):
    def test_restated_title_with_filler_detected(self):
        self.assertTrue(_is_title_repeat(
            "美国制裁国际刑事法院",
            "美国对国际刑事法院实施制裁，可能严重制约其运作能力。",
        ))

    def test_informative_summary_kept(self):
        self.assertFalse(_is_title_repeat(
            "湖北烟花店爆燃12人死亡",
            "10月10日，湖北某烟花店发生爆燃事故，造成12人死亡，调查报告指出系顾客违规试放引发。",
        ))


class ClassifyTest(unittest.TestCase):
    def test_foreign_person_in_china_story_is_international(self):
        self.assertEqual(
            _classify_by_title("人物｜白宫新任发言人：曾在中国生活三年，上周还不在候选名单上"),
            "国际",
        )


class HeadCleanTest(unittest.TestCase):
    def test_column_prefix_removed(self):
        self.assertEqual(
            _shorten_head("人物｜白宫新任发言人：曾在中国生活三年，上周还不在候选名单上"),
            "白宫新任发言人：曾在中国生活三年，上周还不在候选名单上",
        )
        self.assertEqual(
            _shorten_head("专访｜“随机鹦鹉”论文作者、谷歌前AI伦理负责人格布鲁"),
            "“随机鹦鹉”论文作者、谷歌前AI伦理负责人格布鲁",
        )

    def test_tail_fragment_removed(self):
        self.assertEqual(
            _shorten_head("小鹏当选联合国欧洲经济委员会自动驾驶新设工作组秘书长，何小鹏"),
            "小鹏当选联合国欧洲经济委员会自动驾驶新设工作组秘书长",
        )
        self.assertEqual(
            _shorten_head("女生校园内骑车被电动车撞倒离世，家属代理律师"),
            "女生校园内骑车被电动车撞倒离世",
        )

    def test_meaningful_tail_kept(self):
        self.assertEqual(
            _shorten_head("某地违规货车侧翻，司机被刑拘"),
            "某地违规货车侧翻，司机被刑拘",
        )

    def test_normal_head_untouched(self):
        head = "湖北烟花店爆燃12人死亡 调查报告指顾客违规试放引发"
        self.assertEqual(_shorten_head(head), head)


class EngineFlagTest(unittest.TestCase):
    def test_engine_used_returns_string(self):
        self.assertIsInstance(engine_used(), str)


class QaStyleSummaryTest(unittest.TestCase):
    def test_press_conference_qa_rejected(self):
        """记者问答原文不算介绍（当天出现过"问：据报道…中方有何评论？"）。"""
        n = {
            "title": "外交部发言人就巴拿马发生7.6级地震答记者问",
            "summary": "问：据报道，巴拿马10日发生7.6级地震，已发生100余次余震。中方对此有何评论？",
        }
        self.assertIsNone(_rich_summary(n))


class TooShortSummaryTest(unittest.TestCase):
    def test_short_summary_not_padded_with_title(self):
        """正文太短时宁可用要点成行，也不能拼成"标题：重复内容"。"""
        n = {
            "title": "昔日长征路 今朝幸福路（赓续长征精神）",
            "summary": "河南平顶山市叶县充分发掘红色资源。",
        }
        self.assertIsNone(_one_line_summary(n, None))


class ColumnPrefixTest(unittest.TestCase):
    def test_international_feature_prefix_removed(self):
        self.assertEqual(
            _shorten_head("国际特稿：美伊战火延烧也门 沙特与胡塞武装缠斗难休"),
            "美伊战火延烧也门 沙特与胡塞武装缠斗难休",
        )

    def test_fragment_after_colon_truncation_removed(self):
        """先按冒号截断、截出来的部分仍带尾段碎片时要再清一次。"""
        self.assertEqual(
            _shorten_head("女生校园内骑车被电动车撞倒离世，家属代理律师：两车违停，交警复议增加责任"),
            "女生校园内骑车被电动车撞倒离世",
        )


if __name__ == "__main__":
    unittest.main()
