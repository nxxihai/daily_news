"""2026-11-24 用例：恢复推送后发现的两处小瑕疵。

"特朗普视黄仁勋为开放权重人工智能模型捍卫者" 落在国内栏——
_FOREIGN_ANCHORS 里只有国家/组织，没有外国政要姓名，
"外国实体在开头 → 国际"规则接不住它。
"""

import unittest

from summarize import _classify_by_title, _POST_FILTER_BAN  # noqa: E402


class ForeignLeaderTest(unittest.TestCase):
    def test_foreign_leader_opens_international(self):
        self.assertEqual(
            _classify_by_title("特朗普视黄仁勋为开放权重人工智能模型捍卫者"), "国际"
        )

    def test_china_response_stays_domestic(self):
        """中国主语优先，不能被句中"特朗普"拽到国际栏。"""
        self.assertEqual(
            _classify_by_title("中国外交部回应特朗普涉台言论"), "国内"
        )


class BanTest(unittest.TestCase):
    def test_ceo_and_bigtech_banned(self):
        for ban in ("黄仁勋", "OpenAI", "特斯拉", "苹果公司"):
            self.assertIn(ban, _POST_FILTER_BAN)


if __name__ == "__main__":
    unittest.main()