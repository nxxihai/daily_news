"""AI 提炼失败重试的回归用例：AI 失效就丢弃结果，重来一遍再推送。"""

import unittest

import run_daily
import summarize as summarize_mod


class RetryTest(unittest.TestCase):
    """_summarize_with_retry：规则版结果要丢弃，重试拿到 AI 版才返回。"""

    def _run(self, engines):
        seq = list(engines)

        def fake(news_items):
            engine = seq.pop(0)
            summarize_mod.LAST_ENGINE = engine
            summarize_mod.LAST_MODEL = "fake-model" if engine == "llm" else ""
            return f"markdown-from-{engine}"

        original_summarize = run_daily.summarize
        original_delay = run_daily.LLM_RETRY_DELAY
        run_daily.summarize = fake
        run_daily.LLM_RETRY_DELAY = 0          # 测试里不真的等
        try:
            markdown = run_daily._summarize_with_retry(["候选"])
        finally:
            run_daily.summarize = original_summarize
            run_daily.LLM_RETRY_DELAY = original_delay
        return markdown, seq

    def test_first_attempt_llm_no_retry(self):
        markdown, remaining = self._run(["llm"])
        self.assertEqual(markdown, "markdown-from-llm")
        self.assertEqual(remaining, [])            # 没有再尝试第二次

    def test_retry_until_llm(self):
        """第一次退回规则版 → 丢弃 → 第二次拿到 AI 版。"""
        markdown, remaining = self._run(["rule", "llm"])
        self.assertEqual(markdown, "markdown-from-llm")
        self.assertEqual(remaining, [])

    def test_all_attempts_fail_returns_rule_version(self):
        """全都失败：返回最后一次（规则版），由调用方决定推不推。"""
        markdown, remaining = self._run(["rule", "rule", "rule"])
        self.assertEqual(markdown, "markdown-from-rule")
        self.assertEqual(remaining, [])

    def test_attempt_budget_respected(self):
        """尝试次数不超过配置（默认 3 次），不会无限重试。"""
        markdown, remaining = self._run(["rule"] * 5)
        self.assertEqual(markdown, "markdown-from-rule")
        self.assertEqual(len(remaining), 2)         # 5 次配置只试了 3 次


class FallbackSwitchTest(unittest.TestCase):
    def test_push_rule_fallback_default_on(self):
        """默认是"宁可标精简版也不断更"。"""
        from config import PUSH_RULE_FALLBACK

        self.assertIs(PUSH_RULE_FALLBACK, True)


if __name__ == "__main__":
    unittest.main()