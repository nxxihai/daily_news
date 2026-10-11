"""每日新闻早报 - 配置文件。

仓库是公开的：所有 key 都不写死在本文件里，只从两处读取（见下面 _secret）：
1. 环境变量 —— 云端 GitHub Actions 由 Secrets 注入
2. local_config.py —— 本地私密文件（已在 .gitignore，模板 local_config.example.py）

密钥对应的功能（没配就自动跳过，不影响其它功能）：
- 新闻源：天行数据 / 聚合数据（百度热搜、新浪新闻无需 key，开箱即用）
- AI 提炼：DeepSeek 官方 / 智谱 GLM / OpenRouter 免费模型 / 本地 Ollama（按顺序尝试，自动降级）
- 推送：Server酱 + PushPlus 双通道（主通道挂了自动切备用）

运行方式：
    python run_daily.py            # 完整跑一遍并推送
    python run_daily.py --dry-run  # 只采集+提炼，打印结果不推送（先用这个看效果）
"""

import os

# ================= 密钥读取（公开仓库：绝不写死在本文件里） =================
try:
    import local_config as _local          # 本地私密配置，模板见 local_config.example.py
except ImportError:                        # 没有该文件也能正常跑，只是相关功能不启用
    _local = None


def _secret(name):
    """按名字取密钥：环境变量优先 → local_config.py → 空串（对应功能自动跳过）。"""
    return os.environ.get(name) or (getattr(_local, name, "") if _local else "")


# 本地代理开关（详见 local_config.example.py）：
# 有些本地代理（如 Steam++/Watt Toolkit）会用自签 CA 解密 HTTPS，Python 证书库不认它，
# 会导致 LLM 通道 / 新浪等 verify=True 的请求全部 SSL 失败（云端 Actions 不会）。
# 在 local_config.py 里设 BYPASS_SYSTEM_PROXY = True 即可让本机脚本绕过系统代理直连。
if getattr(_local, "BYPASS_SYSTEM_PROXY", False) and not os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = "*"


# ================= 新闻源 =================
# 天行数据：https://www.tianapi.com/ 注册后，在"个人中心"找 key
TIANAPI_KEY = _secret("TIANAPI_KEY")

# 聚合数据：https://www.juhe.cn/ 注册（需实名认证）后拿 key
JUHE_KEY = _secret("JUHE_KEY")

# ================= AI 提炼（按顺序尝试，第一个成功即启用） =================
# 允许直接接入 FreeLLMAPI / 自建 OpenAI 兼容接口，并优先使用配置好的环境变量
FREELLMAPI_API_KEY = _secret("FREELLMAPI_API_KEY") or _secret("FREELLM_API_KEY")
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
    #    https://platform.deepseek.com 注册充值，创建 API Key 后填到环境变量
    #    DEEPSEEK_API_KEY，或写进 local_config.py。约 1-2 元/百万 tokens，
    #    每天跑一次成本可忽略，格式遵循能力强，基本不会"格式不达标"。
    {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": _secret("DEEPSEEK_API_KEY"),
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
        "api_key": _secret("MODELSCOPE_API_KEY"),
        "model": "stepfun-ai/Step-3.7-Flash",
        "timeout": 40,   # 免费推理服务偶发变慢，放宽读取超时并允许重试1次
        "max_retries": 1,
    },
    # 3. 智谱 GLM-4-Flash：永久免费、中文强（国内网络好用；timeout 放宽确保稳定）
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": _secret("ZHIPU_API_KEY"),
        "model": "glm-4-flash",
        "timeout": 40,   # 放宽读取超时并允许重试1次（偶发 400/超时）
        "max_retries": 1,
    },
    # 3b. 智谱新版免费 Flash（glm-4-flash 偶尔报 400 时的备用）
    {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": _secret("ZHIPU_API_KEY"),
        "model": "glm-4.5-flash",
        "timeout": 40,
        "max_retries": 1,
    },
    # 3c. 魔搭 Qwen3-8B 中文基础模型（Step-3.7-Flash 不稳定时的备用）
    {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": _secret("MODELSCOPE_API_KEY"),
        "model": "Qwen/Qwen3-8B",
        "timeout": 40,
        "max_retries": 1,
    },
    # 4. OpenRouter 免费备选：旧 key 已失效(401)，不再写死。
    #    想用请到 https://openrouter.ai/ 换新 key，设环境变量 OPENROUTER_API_KEY
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": _secret("OPENROUTER_API_KEY"),
        "model": "z-ai/glm-5.2:free",
        "timeout": 10,
        "max_retries": 0,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": _secret("OPENROUTER_API_KEY"),
        "model": "minimax/minimax-m3:free",
        "timeout": 10,
        "max_retries": 0,
    },
    {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": _secret("OPENROUTER_API_KEY"),
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
# Server酱（主通道）：https://sct.ftqq.com/ 扫码获取 SendKey，
# 填到环境变量 SERVERCHAN_KEY 或 local_config.py（公开仓库里不要写死！）
SERVERCHAN_KEY = _secret("SERVERCHAN_KEY")

# PushPlus（备用通道）：https://www.pushplus.plus/ 扫码获取 token
PUSHPLUS_TOKEN = _secret("PUSHPLUS_TOKEN")


# ================= 网页版发布（GitHub Pages） =================
# 每天的 markdown 会渲染成 docs/index.html 并推送到仓库，由 Pages 发布成网页版。
# 一次性设置：仓库 Settings → Pages → Source 选 "Deploy from a branch"，
# 分支选 main、目录选 /docs，保存后地址固定为下面这个（永远是最新一期）。
SITE_URL = "https://nxxihai.github.io/daily_news/"


# ================= 语音版（听新闻 MP3） =================
# 每天用 edge-tts（免费，微软自然音色）把当天早报合成一个 MP3，放到 docs/audio/latest.mp3，
# 网页顶部用原生播放器播放（微信内置浏览器也能播、可锁屏后台播放）。
# 合成失败会自动跳过（页面就不带播放器），不影响推送。
TTS_ENABLED = os.environ.get("TTS_ENABLED", "1") != "0"
TTS_VOICE = os.environ.get("TTS_VOICE") or "zh-CN-XiaoxiaoNeural"   # 女声；男声：zh-CN-YunxiNeural
TTS_RATE = os.environ.get("TTS_RATE") or "-10%"                     # 语速，负值更慢（"0%" 为原生速度）


# ================= 内容设置 =================
NEWS_PER_SECTION = 5    # 每类新闻条数（国际/国内/热点 各 5 条，共 15 条）
NEWS_PER_LINE = 60      # 每条"介绍"的目标字数（简报格式：一句话介绍，20~60字）
FALLBACK_EXTRA_PER_SECTION = 3  # AI 全失效时，规则兜底每栏多加 3 条（一句话新闻，共 24 条）
