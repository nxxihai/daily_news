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
# 旧 key 已失效且仓库公开，不再写死；本地想用请设环境变量 JUHE_KEY，云端走 GitHub Secret
JUHE_KEY = os.environ.get("JUHE_KEY") or ""

# ================= AI 提炼（按顺序尝试，第一个成功即启用） =================
# 允许直接接入 FreeLLMAPI / 自建 OpenAI 兼容接口，并优先使用配置好的环境变量
FREELLMAPI_API_KEY = os.environ.get("FREELLMAPI_API_KEY") or os.environ.get("FREELLM_API_KEY") or ""
FREELLMAPI_BASE_URL = (
    os.environ.get("FREELLMAPI_BASE_URL")
    or os.environ.get("FREELLM_BASE_URL")
    or os.environ.get("LLM_BASE_URL")
    or "https://api.freellmapi.com/v1"
)
FREELLMAPI_MODEL = (
    os.environ.get("FREELLMAPI_MODEL")
    or os.environ.get("FREELLM_MODEL")
    or os.environ.get("LLM_MODEL")
    or "qwen3-8b"
)

LLM_PROVIDERS = [
    # 0. DeepSeek 官方（推荐付费通道，稳定且极便宜）：
    #    https://platform.deepseek.com 注册充值，创建 API Key 后填到环境变量 DEEPSEEK_API_KEY
    #    或直接把 key 写在下面 or "" 里。约 1-2 元/百万 tokens，每天跑一次成本可忽略，
    #    格式遵循能力强，基本不会"格式不达标"。
    {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.environ.get("DEEPSEEK_API_KEY") or "",
        "model": "deepseek-chat",
        "timeout": 60,
        "max_retries": 1,
    },
    # 1. FreeLLMAPI / 自建 OpenAI 兼容接口：优先走你自己的模型服务（如果配置了）
    {
        "base_url": FREELLMAPI_BASE_URL,
        "api_key": FREELLMAPI_API_KEY,
        "model": FREELLMAPI_MODEL,
        "timeout": 25,
        "max_retries": 0,
    },
    # 2. 魔搭 ModelScope：国内直连、免费额度，中文强（替代原 DeepSeek 官方）
    # base_url 是 OpenAI 兼容的推理 API 地址；model 用免费的 Step-3.7-Flash（阶跃星辰）
    # 更多可用模型：deepseek-ai/DeepSeek-V4-Flash-0731 / ZhipuAI/GLM-4.7-Flash / Qwen/Qwen3-8B
    {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": os.environ.get("MODELSCOPE_API_KEY") or "",
        "model": "stepfun-ai/Step-3.7-Flash",
        "timeout": 40,   # 免费推理服务偶发变慢，放宽读取超时并允许重试1次
        "max_retries": 1,
    },
    # 3. 智谱 GLM-4-Flash：永久免费、中文强（国内网络好用；timeout 放宽确保稳定）
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.environ.get("ZHIPU_API_KEY") or "",
        "model": "glm-4-flash",
        "timeout": 40,   # 放宽读取超时并允许重试1次（偶发 400/超时）
        "max_retries": 1,
    },
    # 3b. 智谱新版免费 Flash（glm-4-flash 偶尔报 400 时的备用）
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.environ.get("ZHIPU_API_KEY") or "",
        "model": "glm-4.5-flash",
        "timeout": 40,
        "max_retries": 1,
    },
    # 3c. 魔搭 Qwen3-8B 中文基础模型（Step-3.7-Flash 不稳定时的备用）
    {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": os.environ.get("MODELSCOPE_API_KEY") or "",
        "model": "Qwen/Qwen3-8B",
        "timeout": 40,
        "max_retries": 1,
    },
    # 4. OpenRouter 免费备选：旧 key 已失效(401)，不再写死。
    #    想用请到 https://openrouter.ai/ 换新 key，设环境变量 OPENROUTER_API_KEY
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "",
        "model": "z-ai/glm-5.2:free",
        "timeout": 10,
        "max_retries": 0,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "",
        "model": "minimax/minimax-m3:free",
        "timeout": 10,
        "max_retries": 0,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY") or "",
        "model": "nvidia/nemotron-3.5-lightning:free",
        "timeout": 10,
        "max_retries": 0,
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
NEWS_PER_LINE = 60      # 每条"介绍"的目标字数（简报格式：一句话介绍，20~60字）
FALLBACK_EXTRA_PER_SECTION = 3  # AI 全失效时，规则兜底每栏多加 3 条（一句话新闻，共 24 条）
