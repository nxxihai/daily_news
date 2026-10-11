"""旧闻混入的回归用例：10-11 的早报里出现了 10-09 的新闻。

两个漏洞：
1. filter.MAX_NEWS_AGE_DAYS 用"整天数"取整，不到 48 小时但跨两天的旧闻会漏过；
   且没有发布时间字段的源（联合早报/澎湃等）完全不做日期过滤
2. 提示词只要求"排除 3 天以前"，前天（10-09）属于允许范围
"""

import unittest
from datetime import datetime, timedelta, timezone

from filter import dates_in_text, filter_by_date
from summarize import _drop_stale_items

BJT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 11, 7, 30, tzinfo=BJT)


class DatesInTextTest(unittest.TestCase):
    def test_parse_month_day(self):
        self.assertEqual([d.day for d in dates_in_text("美国国务院10月9日宣布制裁", NOW.date())], [9])

    def test_no_date_returns_empty(self):
        self.assertEqual(dates_in_text("没有日期的标题", NOW.date()), [])

    def test_year_rollover(self):
        """1 月初提到 12 月底，应推断为去年。"""
        self.assertEqual(
            dates_in_text("12月30日消息", datetime(2027, 1, 3).date())[0].year, 2026
        )


class NoTimestampOldNewsTest(unittest.TestCase):
    """没有发布时间字段的源：靠文本里的日期兜底。"""

    def _item(self, title, summary):
        return {"title": title, "summary": summary, "source": "测试"}

    def test_two_days_ago_dropped(self):
        today = datetime.now(BJT).date()
        old = today - timedelta(days=2)
        items = [
            self._item("美国宣布制裁国际刑事法院",
                       f"美国国务院{old.month}月{old.day}日宣布对ICC实施制裁。"),
            self._item("今天发生的新闻", "今天发布的新政策。"),
        ]
        self.assertEqual([n["title"] for n in filter_by_date(items)], ["今天发生的新闻"])

    def test_yesterday_kept(self):
        today = datetime.now(BJT).date()
        yesterday = today - timedelta(days=1)
        items = [
            self._item("昨天的新闻",
                       f"{yesterday.month}月{yesterday.day}日消息，某地发生事故。"),
        ]
        self.assertEqual(len(filter_by_date(items)), 1)


class StaleSectionTest(unittest.TestCase):
    def test_old_date_only_item_dropped(self):
        sections = {
            "国际": [
                ("美国宣布制裁国际刑事法院",
                 "美国国务院10月9日宣布对ICC实施制裁，此举可能严重制约该机构日常运作能力。"),
                ("沙特利雅得机场遭袭致12死309伤",
                 "沙特民航总局11日凌晨确认，利雅得机场10日遭袭，死伤者含多国公民。"),
            ]
        }
        out = _drop_stale_items(sections, now=NOW)
        self.assertEqual([t for t, _ in out["国际"]], ["沙特利雅得机场遭袭致12死309伤"])

    def test_undated_item_kept(self):
        sections = {"热点": [("某地发生一起事故", "相关部门已到场处置。")]}
        self.assertEqual(len(_drop_stale_items(sections, now=NOW)["热点"]), 1)

    def test_mixed_dates_kept(self):
        """同时提到今天/昨天与更早日期 → 是在讲当天的事，保留。"""
        sections = {
            "国际": [("巴拿马强震余震不断", "10月10日发生7.6级地震，11日又发生多次余震。")]
        }
        self.assertEqual(len(_drop_stale_items(sections, now=NOW)["国际"]), 1)


if __name__ == "__main__":
    unittest.main()
