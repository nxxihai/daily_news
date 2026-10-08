"""针对 2026-09-02 早报质量问题的回归测试。

覆盖四类已修复的问题：
1. 股东增减持等个股资本运作漏网（天工国际增持）
2. 外国公司财年/财季财报混入候选池（戴尔财报）
3. 网络热梗式标题未被拦截（"傻鸟承包全网笑点"）
4. 摘要=标题复述、同一热点主线（美伊冲突/吉隆救援）连续刷屏
"""
import unittest

from filter import filter_quality, is_low_quality
from summarize import (
    _is_same_event,
    _is_title_repeat,
    _one_line_summary,
    _rich_summary,
    _same_mainline,
    rule_summary,
    sections_to_markdown,
)

# 应被拦截的低质样本（真实今日推送中出现过 + 同日演练发现）
LOW_QUALITY_SAMPLES = [
    "天工国际获天工控股连续两日大手笔增持 涉资超637万港元",
    "戴尔科技集团：2027财年第二财季营收470亿美元，同比增长58%",
    "顶流“傻鸟”承包了全网笑点",
    # —— 2026-09-02 dry-run 发现的金融专业噪音 ——
    "苏州银行：2026年第一期金融债券发行完毕",
    "孙丽广州银行董事任职资格获批",
    "伯克希尔CEO阿贝尔：二季度增持后，Alphabet已是AI领域“重要参与者”",
    # —— 规则兜底版发现的游戏/数码导购软文 ——
    "学生党开黑联机加速器怎么选？竞技网游延迟优化与性价比对比",
    "2026年三款高性价比游戏手机核心体验对比",
    "大电池 + 游戏性能，拯救者 Y70 新一代游戏党可以选吗",
    "重新定义桌面用光 从单纯照亮台面，到聚焦人眼体验",
]

# 应保留的样本
KEEP_SAMPLES = [
    "美军称完成一波对伊朗军事目标打击",
    "秘鲁宣布与伊朗断绝外交关系",
    "习近平出席“上海合作组织+”会议并发表重要讲话",
    "家长反对交600元请保洁扫教室被禁言",
    "云南文山12级台风致人伤亡系谣言",
]

# 模拟真实采集条目（带源简介），用于 rule_summary 降级演练
MOCK_ITEMS = [
    {"title": "美军称完成一波对伊朗军事目标打击",
     "summary": "美国国防部称已完成对伊朗境内军事目标的打击，德黑兰等多地夜间传出爆炸声，伤亡与损失情况暂未公布。",
     "source": "新浪新闻", "category": "国际要闻"},
    {"title": "伊朗多地传出爆炸声",
     "summary": "伊朗多座城市夜间传出爆炸声，当地媒体称与美军打击有关，官方尚未发布完整回应。",
     "source": "新浪新闻", "category": "国际要闻"},
    {"title": "秘鲁宣布与伊朗断绝外交关系",
     "summary": "秘鲁外交部宣布与伊朗断绝外交关系，成为本轮地区局势中又一个与伊朗切割的拉美国家。",
     "source": "联合早报", "category": "国际要闻"},
    {"title": "习近平出席“上海合作组织+”会议并发表重要讲话",
     "summary": "国家主席习近平在北京出席“上海合作组织+”会议并发表重要讲话，呼吁各方共同维护地区和平稳定。",
     "source": "新浪新闻", "category": "国内"},
    {"title": "华为、小米、荣耀回应涨价",
     "summary": "针对多款手机涨价传闻，华为、小米、荣耀相继回应称与芯片及元器件成本上涨有关，具体调价以官方为准。",
     "source": "新浪新闻", "category": "国内"},
    {"title": "云南文山12级台风致人伤亡系谣言",
     "summary": "云南文山州网信部门辟谣，网传“12级台风致人伤亡”为不实信息，当地目前无灾情报告。",
     "source": "新浪新闻", "category": "国内"},
    {"title": "家长反对交600元请保洁扫教室被禁言",
     "summary": "某小学家委会提议每生交600元请保洁打扫教室，反对家长称被群内禁言，当地教育部门已介入了解。",
     "source": "新浪新闻", "category": "国内"},
    {"title": "国内品牌金饰价格高位回落",
     "summary": "国际金价从高位回落，周大福、老凤祥等品牌足金价格较上周高点每克下调数十元。",
     "source": "新浪新闻", "category": "国内"},
    {"title": "救援！G216线受冲毁道路已抢通至距吉隆口岸1公里以内",
     "summary": "受强降雨影响中断的G216线抢通工作推进至距吉隆口岸1公里内，抢修人员正做最后冲刺。",
     "source": "央视新闻", "category": "热点"},
    {"title": "搜救犬在吉隆口岸搜救累到趴地",
     "summary": "在吉隆口岸参与搜救的搜救犬因连续作业体力透支趴地休息，训导员称将安排其轮换。",
     "source": "人民日报", "category": "社会"},
    {"title": "女子携儿子闺蜜出游 酒后跳河失联",
     "summary": "一女子带儿子与闺蜜出游期间酒后失联，家属称其疑似跳河，多方救援力量正沿河展开搜救。",
     "source": "新浪新闻", "category": "社会"},
    {"title": "金价下跌 油价大涨",
     "summary": "受中东冲突局势影响，国际油价大涨，避险情绪变化带动金价从高位下跌。",
     "source": "新浪新闻", "category": "财经"},
    # —— 以下三条应被 filter_quality 挡在候选池外，不会进入 summarize ——
    {"title": "天工国际获天工控股连续两日大手笔增持 涉资超637万港元",
     "summary": "天工控股连续两日增持天工国际股份，合计涉资超过637万港元。",
     "source": "新浪新闻", "category": "国际要闻"},
    {"title": "戴尔科技集团：2027财年第二财季营收470亿美元，同比增长58%",
     "summary": "戴尔科技集团发布2027财年第二财季财报，营收470亿美元，同比增长58%。",
     "source": "新浪新闻", "category": "国际要闻"},
    {"title": "顶流“傻鸟”承包了全网笑点",
     "summary": "城市水边的夜鹭因呆萌形象走红网络，成为观鸟圈顶流，网友称其承包了全网笑点。",
     "source": "百度热搜", "category": "热点"},
]


class TestFilterLowQuality(unittest.TestCase):
    """源头过滤器应拦下三类低质样本。"""

    def test_low_quality_flagged(self):
        for title in LOW_QUALITY_SAMPLES:
            self.assertTrue(is_low_quality(title), f"应判定为低质: {title}")

    def test_filter_quality_drops_them(self):
        items = [
            {"title": t, "summary": "", "source": "测试", "category": "热点"}
            for t in LOW_QUALITY_SAMPLES
        ]
        kept = filter_quality(items)
        self.assertEqual(kept, [], "低质样本应全部被滤掉")

    def test_normal_samples_kept(self):
        items = [
            {"title": t, "summary": "这是一段足够长、非复述的正常新闻简介内容，用于验证不被误杀。",
             "source": "测试", "category": "热点"}
            for t in KEEP_SAMPLES
        ]
        kept = filter_quality(items)
        self.assertEqual(len(kept), len(KEEP_SAMPLES), "正常新闻不应被误杀")


class TestTitleRepeat(unittest.TestCase):
    """摘要复述标题检测。"""

    def test_repeat_detected(self):
        self.assertTrue(
            _is_title_repeat("美军称完成一波对伊朗军事目标打击",
                             "美军对伊朗军事目标进行打击。")
        )
        self.assertTrue(
            _is_title_repeat("伊朗多地传出爆炸声", "伊朗多地传出爆炸声。")
        )
        self.assertTrue(
            _is_title_repeat(
                "天工国际获天工控股连续两日大手笔增持 涉资超637万港元",
                "天工控股连续增持天工国际股份，总金额超637万港元。")
        )

    def test_informative_not_repeat(self):
        self.assertFalse(
            _is_title_repeat(
                "美军称完成一波对伊朗军事目标打击",
                "美国国防部称已完成对伊朗境内军事目标的打击，伊朗多地传出爆炸声，伤亡情况暂未公布。")
        )

    def test_rich_summary_requires_substance(self):
        empty = {"title": "伊朗多地传出爆炸声", "summary": "伊朗多地传出爆炸声。"}
        self.assertIsNone(_rich_summary(empty))
        rich = {"title": "伊朗多地传出爆炸声",
                "summary": "伊朗多座城市夜间传出爆炸声，当地媒体称与美军打击有关，官方尚未回应。"}
        self.assertIsNotNone(_rich_summary(rich))


class TestSameMainline(unittest.TestCase):
    """同一热点主线收敛：同地区 + 同动作域合并为一条。"""

    def test_iran_military_merged(self):
        self.assertTrue(
            _same_mainline("美军称完成一波对伊朗军事目标打击", "伊朗多地传出爆炸声")
        )
        self.assertTrue(
            _is_same_event("美军称完成一波对伊朗军事目标打击", "",
                           "伊朗多地传出爆炸声", "")
        )

    def test_iran_diplomacy_kept_separate(self):
        self.assertFalse(
            _same_mainline("美军称完成一波对伊朗军事目标打击", "秘鲁宣布与伊朗断绝外交关系")
        )

    def test_jilong_rescue_merged(self):
        self.assertTrue(
            _same_mainline("救援！G216线受冲毁道路已抢通至距吉隆口岸1公里以内",
                           "搜救犬在吉隆口岸搜救累到趴地")
        )

    def test_unrelated_not_merged(self):
        self.assertFalse(
            _same_mainline("美军称完成一波对伊朗军事目标打击", "金价下跌 油价大涨")
        )


class TestTruncatedSummary(unittest.TestCase):
    """源简介被平台硬截断成半句时，不应补个句号充当完整摘要。"""

    def test_truncated_summary_rejected(self):
        item = {
            "title": "重新定义桌面用光",
            "summary": "数字生活全面渗透、屏幕使用时长持续攀升的当下，"
                       "数字视疲劳已然成为全民普遍性用眼问题，"
                       "长期以来消费电子行业的竞争焦点始终锁定在显示器分",
        }
        self.assertIsNone(_rich_summary(item))
        self.assertIsNone(_one_line_summary(item, 80))

    def test_complete_summary_kept(self):
        item = {
            "title": "泉州市教育局：紧急停课",
            "summary": "受台风“沙德尔”及弱冷空气共同影响，预计2至4日泉州市有持续性暴雨，"
                       "致灾风险大，全市中小学校停课。",
        }
        self.assertIsNotNone(_rich_summary(item))
        self.assertIsNotNone(_one_line_summary(item, 80))


class TestBareTitleRendering(unittest.TestCase):
    """无简介源（联合早报等）以标题直接成行，不应出现"标题：标题"重复。"""

    def test_bare_title_no_double(self):
        sections = {
            "国际": [
                ("秘鲁宣布与伊朗断绝外交关系", "秘鲁宣布与伊朗断绝外交关系。"),
                ("美军称完成一波对伊朗军事目标打击",
                 "美国国防部称已完成对伊朗境内军事目标的打击，伊朗多地传出爆炸声，伤亡暂未公布。"),
            ],
            "国内": [],
            "热点": [],
        }
        out = sections_to_markdown(sections)
        self.assertIn("**秘鲁宣布与伊朗断绝外交关系**", out)
        # 不出现 "标题：标题" 式的重复
        self.assertNotIn("**秘鲁宣布与伊朗断绝外交关系**：秘鲁宣布与伊朗", out)
        # 有实质摘要的正常条目保持两行格式（要点行 + 介绍行）
        self.assertIn("美国国防部称已完成", out)


class TestRuleSummaryQuality(unittest.TestCase):
    """降级输出不应再包含低质条目、不应对同主线刷屏。"""

    @classmethod
    def setUpClass(cls):
        cls.mock_items = filter_quality(list(MOCK_ITEMS))
        cls.out = rule_summary(cls.mock_items, per_section=5)

    def test_low_quality_excluded(self):
        for bad in ["天工国际", "戴尔", "傻鸟", "承包了全网笑点"]:
            self.assertNotIn(bad, self.out, f"输出不应包含: {bad}")

    def test_kept_content_present(self):
        self.assertIn("美军", self.out)
        self.assertIn("秘鲁", self.out)
        self.assertIn("家长反对交600元", self.out)

    def test_no_title_repeat_summaries(self):
        # 每行摘要不应是标题的简单复述（输出形如 "1. **标题**：摘要"）
        for line in self.out.splitlines():
            if "**" not in line or "：" not in line:
                continue
            body = line.split("：", 1)[1]
            # 输出中摘要有实质内容（至少 15 字），防止整行退化为标题复述
            self.assertGreaterEqual(len(body), 15, f"摘要过短/复述: {line}")

    def test_no_mainline_flood(self):
        # 伊朗相关（美伊冲突主线）标题不超过 2 条，吉隆救援只保留 1 条
        iran_count = sum(1 for line in self.out.splitlines()
                         if "**" in line and "伊朗" in line)
        self.assertLessEqual(iran_count, 2, f"伊朗主线不应刷屏: {self.out}")
        jilong_count = sum(1 for line in self.out.splitlines()
                           if "**" in line and "吉隆" in line)
        self.assertLessEqual(jilong_count, 1, f"吉隆救援只应保留1条: {self.out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
