"""2026-10-13 用户反馈后的补充用例。

问题：国内栏里"中创新航：中国证监会就公司实施H股全流通出具备案通知书"
"比亚迪第15万台新能源商用车下线""喀纳斯棕熊…网信办：网传内容系AI合成"
三条把国内要闻挤掉了（用户：国内内容不好，还没热点的好）。
"""

import unittest

from filter import importance_score  # noqa: E402
from summarize import _POST_FILTER_BAN, _fill_missing_sections  # noqa: E402


class BanListTest(unittest.TestCase):
    def test_capital_market_banned(self):
        for ban in ("H股", "A股", "增持", "减持", "股价", "营收", "备案通知书"):
            self.assertIn(ban, _POST_FILTER_BAN)

    def test_corporate_pr_banned(self):
        for ban in ("万台", "万辆", "产能", "量产"):
            self.assertIn(ban, _POST_FILTER_BAN)

    def test_rumor_correction_banned(self):
        for ban in ("辟谣", "AI合成", "网传"):
            self.assertIn(ban, _POST_FILTER_BAN)


class FillSectionTest(unittest.TestCase):
    """三条噪声都不能靠"规则补位"重新混进早报。"""

    NOISY = [
        {"title": "中创新航：中国证监会就公司实施H股全流通出具备案通知书",
         "summary": "公司公告称已收到备案通知书，将推进相关工作。", "category": "财经"},
        {"title": "比亚迪第15万台新能源商用车下线，正加快攻坚千线重卡电动化",
         "summary": "公司在仪式现场表示将继续扩大产能。", "category": "财经"},
        {"title": "喀纳斯棕熊索食险酿大祸？新疆布尔津县委网信办：网传内容系AI合成",
         "summary": "网信办称网传内容系AI合成，已辟谣。", "category": "社会"},
    ]

    def test_noisy_items_never_filled_back(self):
        sections = {"国际": [], "国内": [], "热点": []}
        filled = _fill_missing_sections(sections, self.NOISY, per_section=5)
        picked = [t for items in filled.values() for t, _ in items]
        self.assertEqual(picked, [], f"不该补进早报：{picked}")


class ImportanceScoreTest(unittest.TestCase):
    def test_policy_news_above_filler(self):
        policy = {"title": "铁路科技创新“十五五”规划发布", "source": "澎湃新闻", "category": "国内"}
        filler = {"title": "某地举办夏季业余体育联赛开幕式", "source": "澎湃新闻", "category": "国内"}
        self.assertGreater(importance_score(policy), importance_score(filler))


if __name__ == "__main__":
    unittest.main()