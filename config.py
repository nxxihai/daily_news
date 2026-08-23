"""每日新闻早报 - 配置文件。

所有 key 在此填写，填了才启用对应功能：
- 新闻源：天行数据 / 聚合数据（百度热搜、新浪新闻无需 key，开箱即用）
- AI 提炼：OpenRouter 免费模型 / 本地 Ollama / DeepSeek（按顺序尝试，自动降级）
- 推送：Server酱 + PushPlus 双通道（主通道挂了自动切备用）

运行方式：
    python run_daily.py            # 完整跑一遍并推送
    python run_daily.py --dry-run  # 只采集+提炼，打印结果不推送（先用这个看效果）
"""

import os

# ================= 新闻源 =================
# 天行数据：https://www.tianapi.com/ 注册后，在"个人中心"找 key
# 云端部署（GitHub Actions）时可通过 Secrets 注入同名环境变量覆盖此处默认值
TIANAPI_KEY = os.environ.get("TIANAPI_KEY") or "6e3289c86b1e63a9d6aff2af1be34b16"

# 聚合数据：https://www.juhe.cn/ 注册（需实名认证）后拿 key
JUHE_KEY = os.environ.get("JUHE_KEY") or ""


# ================= AI 提炼（按顺序尝试，第一个成功即启用） =================
LLM_PROVIDERS = [
    # 智谱 GLM-4.7-Flash：永久免费、中文强，第一优先
    # 注册 https://open.bigmodel.cn/ 实名后拿 key，配到环境变量 ZHIPU_API_KEY
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.environ.get("ZHIPU_API_KEY") or "",
        "model": "glm-4.7-flash",
        "timeout": 30,
    },
    # DeepSeek 官方（质量稳定，GLM 失败时的备选）
    # 默认读取系统环境变量 OPENAI_API_KEY（你智能伴侣项目里已在用），也可直接填 key
    {
        "base_url": "https://api.deepseek.com",
        "api_key": os.environ.get("OPENAI_API_KEY") or "",
        "model": "deepseek-v4-flash",
        # 关闭推理思考：v4-pro 默认思考模式要 30 秒+，关掉后 3~4 秒，质量足够
        "extra_body": {"thinking": {"type": "disabled"}},
        "timeout": 30,
    },
    # OpenRouter 免费模型（DeepSeek 失败时的备选）
    # 免费模型是共享池，高峰期可能被限流(429)，故配置多个候选，失败自动换下一个
    # 可在官网 https://openrouter.ai/models 挑其他 :free 结尾的模型追加
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "z-ai/glm-5.2:free",                      # GLM 5.2 中文强
        "timeout": 10,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "nvidia/nemotron-3.5-lightning:free",    # NVIDIA 快速模型
        "timeout": 10,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "google/gemma-4-31b-it:free",            # Gemma 4
        "timeout": 10,
    },
    # 本地 Ollama（可选）：装了 Ollama 并 pull 了模型即可用，完全免费离线
    {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3:8b",
    },
]


# ================= 微信推送 =================
# Server酱（主通道）：https://sct.ftqq.com/ 扫码获取 SendKey
# 云端部署时可用环境变量 SERVERCHAN_KEY 覆盖（本地留默认值照常运行）
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY") or "SCT398775TS2MEMjaZVoZ9TX4jDEfGb9Wi"

# PushPlus（备用通道）：https://www.pushplus.plus/ 扫码获取 token
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN") or ""


# ================= 内容设置 =================
NEWS_PER_SECTION = 5    # 每类新闻条数（国际/国内/热点 各 5 条，共 15 条）
NEWS_PER_LINE = 60      # 每条摘要目标字数（40~60 字）
