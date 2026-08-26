"""每日新闻早报 - 配置文件。

所有 key 在此填写，填了才启用对应功能：
- 新闻源：天行数据 / 聚合数据（百度热搜、新浪新闻无需 key，开箱即用）
- AI 提炼：DeepSeek 官方 / 智谱 GLM / OpenRouter 免费模型 / 本地 Ollama（按顺序尝试，自动降级）
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
JUHE_KEY = os.environ.get("JUHE_KEY") or "b569e931c55389e18e561b3d8476703a"


# ================= AI 提炼（按顺序尝试，第一个成功即启用） =================
LLM_PROVIDERS = [
    # 0. 魔搭 ModelScope：国内直连、免费额度，中文强，优先使用（替代原 DeepSeek 官方）
    # base_url 是 OpenAI 兼容的推理 API 地址；model 用免费的 Step-3.7-Flash（阶跃星辰）
    # 更多可用模型：deepseek-ai/DeepSeek-V4-Flash-0731 / ZhipuAI/GLM-4.7-Flash / Qwen/Qwen3-8B
    {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": os.environ.get("MODELSCOPE_API_KEY") or "",
        "model": "stepfun-ai/Step-3.7-Flash",
        "timeout": 60,
        "max_retries": 2,
    },
    # 1. 智谱 GLM-4-Flash：永久免费、中文强（国内网络好用；海外节点可能超时）
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.environ.get("ZHIPU_API_KEY") or "",
        "model": "glm-4-flash",
        "timeout": 30,
        "max_retries": 2,
    },
    # 3. OpenRouter DeepSeek V3 免费版（可能限流，作为备选）
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "deepseek/deepseek-chat-v3-0324:free",
        "timeout": 30,
        "max_retries": 1,
    },
    # 4. OpenRouter 其他免费模型（DeepSeek 失败时的备选）
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "z-ai/glm-4.7-flash:free",
        "timeout": 20,
        "max_retries": 1,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "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",
        "model": "nvidia/nemotron-3.5-lightning:free",
        "timeout": 20,
        "max_retries": 1,
    },
    # 5. 本地 Ollama（可选）：装了 Ollama 并 pull 了模型即可用，完全免费离线
    {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3:8b",
        "timeout": 60,
        "max_retries": 0,
    },
]

# LLM 调用重试等待时间（秒），指数退避
RETRY_BASE_DELAY = 3


# ================= 微信推送 =================
# Server酱（主通道）：https://sct.ftqq.com/ 扫码获取 SendKey
# 云端部署时可用环境变量 SERVERCHAN_KEY 覆盖（本地留默认值照常运行）
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY") or "SCT398775TS2MEMjaZVoZ9TX4jDEfGb9Wi"

# PushPlus（备用通道）：https://www.pushplus.plus/ 扫码获取 token
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN") or ""


# ================= 内容设置 =================
NEWS_PER_SECTION = 5    # 每类新闻条数（国际/国内/热点 各 5 条，共 15 条）
NEWS_PER_LINE = 50      # 每条摘要目标字数（50 字以内，信息更精炼）
