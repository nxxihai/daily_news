"""2026-11-25 用例：GitHub schedule 双时间点 + 防重复门。

GitHub 08:10 的调度被跳过导致断更，加 08:45 兜底；
为避免两次调度（以及手动触发）重复推送，schedule 时检查页面日期。
"""

import os
import tempfile
import unittest
from pathlib import Path

import run_daily  # noqa: E402


class AlreadyPushedTest(unittest.TestCase):
    def setUp(self):
        self.orig_base = run_daily.BASE_DIR
        self.tmp = Path(tempfile.mkdtemp(prefix="pushed_"))
        (self.tmp / "docs").mkdir()
        run_daily.BASE_DIR = self.tmp

    def tearDown(self):
        run_daily.BASE_DIR = self.orig_base

    def test_no_page_means_not_pushed(self):
        self.assertFalse(run_daily._already_pushed_today("2026-11-25"))

    def test_today_page_means_pushed(self):
        (self.tmp / "docs" / "index.html").write_text(
            '<p class="meta">更新于 2026-11-25 08:47（北京时间）</p>', encoding="utf-8")
        self.assertTrue(run_daily._already_pushed_today("2026-11-25"))

    def test_yesterday_page_means_not_pushed(self):
        (self.tmp / "docs" / "index.html").write_text(
            '<p class="meta">更新于 2026-11-24 12:26（北京时间）</p>', encoding="utf-8")
        self.assertFalse(run_daily._already_pushed_today("2026-11-25"))

    def test_schedule_event_guard(self):
        """schedule 事件遇到当天已推送 → main() 直接跳过（不采集不推送）。"""
        (self.tmp / "docs" / "index.html").write_text(
            '<p class="meta">更新于 2026-11-25 08:47（北京时间）</p>', encoding="utf-8")
        prev = os.environ.get("GITHUB_EVENT_NAME")
        try:
            os.environ["GITHUB_EVENT_NAME"] = "schedule"
            self.assertEqual(run_daily.main(), 0)
        finally:
            if prev is None:
                os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                os.environ["GITHUB_EVENT_NAME"] = prev


if __name__ == "__main__":
    unittest.main()