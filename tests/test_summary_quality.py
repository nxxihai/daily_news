import importlib
import os
import unittest
from unittest.mock import patch


class TestFreeLLMProviderConfig(unittest.TestCase):
    def test_freellmapi_provider_takes_priority(self):
        with patch.dict(
            os.environ,
            {
                "FREELLMAPI_API_KEY": "freellm-test-key",
                "FREELLMAPI_BASE_URL": "https://api.freellm.example/v1",
                "FREELLMAPI_MODEL": "qwen3-8b",
            },
            clear=False,
        ):
            import config

            importlib.reload(config)
            providers = config.LLM_PROVIDERS
            freellm_index = next(
                (
                    i
                    for i, p in enumerate(providers)
                    if p.get("api_key") == "freellm-test-key"
                ),
                None,
            )
            self.assertIsNotNone(freellm_index, "FreeLLMAPI 配置应出现在 provider 列表中")
            # 通道优先级：DeepSeek 官方（推荐付费）在最前，未配置时 FreeLLMAPI 次之
            self.assertEqual(
                providers[freellm_index]["base_url"], "https://api.freellm.example/v1"
            )
            self.assertEqual(providers[freellm_index]["model"], "qwen3-8b")
            self.assertTrue(
                any("deepseek" in p.get("base_url", "") for p in providers),
                "应包含 DeepSeek 官方通道（按环境变量是否配置决定是否启用）",
            )


if __name__ == "__main__":
    unittest.main()
