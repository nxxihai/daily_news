"""2026-10-13 第三轮用例：成稿阶段暴露的三个问题。

1. 同一事件两条："湖北水利发展集团总经理周俊主动投案"+"…总经理周俊接受审查调查"
   → 成稿只过了同事件判断，没走同主线收敛
2. 中国是主语却被判国际："中国外交部对巴拉圭…反制""李强称中国愿同越南…"
3. 栏目超编：LLM 给了国际 6 条 / 国内 4 条
"""

import unittest

from summarize import (  # noqa: E402
    _classify_by_title,
    _merge_same_mainline_sections,
    sections_to_markdown,
)


class SameMainlineTest(unittest.TestCase):
    def test_corruption_case_merged(self):
        sections = {
            "国内": [
                ("哈药股份总裁芦传有被立案留置", "公司公告称已被监察机关立案审查并留置。"),
                ("湖北水利发展集团总经理周俊主动投案接受审查调查",
                 "周俊因涉嫌严重违纪违法主动投案。"),
                ("湖北水利发展集团党委副书记、总经理周俊接受审查调查",
                 "通报称周俊正接受纪律审查和监察调查。"),
            ]
        }
        merged = _merge_same_mainline_sections(sections)["国内"]
        titles = [t for t, _ in merged]
        self.assertEqual(len(merged), 2, f"同一事件应收敛为一条：{titles}")
        # 保留下来的那条必须还是周俊（而不是把事件删没了）
        self.assertEqual(sum("周俊" in t for t in titles), 1, titles)


class ChinaSubjectTest(unittest.TestCase):
    def test_diplomatic_action_is_domestic(self):
        self.assertEqual(
            _classify_by_title("中国外交部对巴拉圭通信和技术部长采取反制措施"), "国内"
        )

    def test_premier_statement_is_domestic(self):
        self.assertEqual(
            _classify_by_title("李强称中国愿同越南推动AI领域合作提质增效"), "国内"
        )

    def test_chinese_tourist_is_not_state_subject(self):
        """主语是中国人而非国家时，不走"中国主体"规则。"""
        self.assertNotEqual(
            _classify_by_title("5名中国籍男子在泰国持枪入室抢劫被通缉"), "国内"
        )


class SectionCapTest(unittest.TestCase):
    def test_cap_at_news_per_section(self):
        sections = {
            "国际": [(f"国际第{i}条消息内容占位", "这里是介绍内容占位文本。") for i in range(1, 8)],
            "国内": [],
            "热点": [],
        }
        md = sections_to_markdown(sections)
        self.assertEqual(md.count("国际第"), 5, md)


if __name__ == "__main__":
    unittest.main()