"""2026-10-13 补充：地区锚点表要覆盖完整国家名。

"中国大陆制裁巴拉圭官员"与"外交部对巴拉圭部长采取反制措施"是同一事件，
但旧 _MAINLINE_REGIONS 没收录"巴拉圭"，两条都进了早报。
"""

import unittest

from summarize import _mainline_regions, _same_mainline  # noqa: E402


class RegionAnchorTest(unittest.TestCase):
    def test_small_country_anchor_found(self):
        self.assertIn("巴拉圭", _mainline_regions("中国大陆制裁台湾两邦交国巴拉圭和伯利兹官员"))

    def test_sanction_and_retaliation_are_same_mainline(self):
        self.assertTrue(_same_mainline(
            "中国大陆制裁台湾两邦交国巴拉圭和伯利兹官员",
            "外交部：对巴拉圭通信和技术部长比利亚特采取反制措施",
        ))

    def test_unrelated_countries_not_merged(self):
        self.assertFalse(_same_mainline(
            "俄罗斯副总理称俄美正谈判柴油供应事宜",
            "智利中部发生6.2级地震",
        ))


if __name__ == "__main__":
    unittest.main()