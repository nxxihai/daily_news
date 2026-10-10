"""本地私密配置模板：复制成 local_config.py 后填值（该文件已 gitignore，不会被提交）。

读取优先级：环境变量 > local_config.py
- 本地手动 / 计划任务运行：靠这个文件
- 云端 GitHub Actions：靠仓库 Secrets 注入同名环境变量

只填要用的即可，留空或不写都代表"该功能不启用"。
"""

# 天行数据：https://www.tianapi.com/
TIANAPI_KEY = ""

# 聚合数据：https://www.juhe.cn/
JUHE_KEY = ""

# AI 提炼（按 config.py 里 LLM_PROVIDERS 的顺序尝试，配一个就够）
DEEPSEEK_API_KEY = ""      # https://platform.deepseek.com（推荐，稳定便宜）
MODELSCOPE_API_KEY = ""    # 魔搭 ModelScope（免费）
ZHIPU_API_KEY = ""         # 智谱 GLM（免费）
OPENROUTER_API_KEY = ""    # OpenRouter（免费模型）

# 微信推送
SERVERCHAN_KEY = ""        # https://sct.ftqq.com/ 的 SendKey（主通道）
PUSHPLUS_TOKEN = ""        # https://www.pushplus.plus/ 的 token（备用通道）

# 本机跑脚本时是否绕开系统代理（默认不开）。
# 开着 Steam++/Watt Toolkit 这类会解密 HTTPS 的本地代理时，Python 的证书库不认它的
# 自签 CA，LLM 通道和新浪源会报 CERTIFICATE_VERIFY_FAILED；设为 True 即可直连。
# 如果以后改用梯子代理（某些源必须走代理），请保持 False。
# BYPASS_SYSTEM_PROXY = True
