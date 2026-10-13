"""2026-10-13 分类边角用例。

"内蒙古呼伦贝尔发布鼠疫风险提示" 曾被判成国际——国际国家表里的"蒙古"
被"内蒙古"子串命中。修法：国际表用"蒙古国"，国内表补省级行政区。
"""

import unittest

from summarize import _classify_by_title  # noqa: E402


class RegionBoundaryTest(unittest.TestCase):
    def test_inner_mongolia_is_domestic(self):
        self.assertEqual(
            _classify_by_title("内蒙古呼伦贝尔发布鼠疫风险提示"), "国内"
        )

    def test_mongolia_country_still_international(self):
        self.assertEqual(_classify_by_title("蒙古国议会通过对外投资新规"), "国际")

    def test_xinjiang_never_goes_international(self):
        """含"多地"（国际表用于"多地地震"）也不能把国内新闻拽进国际栏。

        国内/热点两栏都说得过去，重点是不能误判成国际。
        """
        self.assertIn(
            _classify_by_title("新疆多地启动秋收作业"), ("国内", "热点")
        )


if __name__ == "__main__":
    unittest.main()