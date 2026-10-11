"""创建 Windows 计划任务：每天 08:10 运行 run_daily.py 并推送微信早报。

用法：python setup_schedule.py
说明：任务以当前用户身份创建，仅在用户登录时运行（可继承你的环境变量，
      如 OPENAI_API_KEY）。如需"未登录也运行"，请用任务计划程序 GUI 修改。
      已存在同名任务会被覆盖（/F），所以改时间后重跑本脚本即可生效。
      选 08:10 而不是 08:00：AI 免费额度按 UTC 0 点（北京 08:00）重置，
      早于 8:10 容易撞上额度用尽、整篇退化成规则版。
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPT_DIR / "run_daily.py"
TASK_NAME = "DailyNewsReport"

# /TR 参数：带引号的完整命令（路径含空格，schtasks 用嵌套引号解析）
command = f'"{sys.executable}" "{SCRIPT}"'

create_cmd = [
    "schtasks", "/Create",
    "/TN", TASK_NAME,
    "/TR", command,
    "/SC", "DAILY",      # 每天
    "/ST", "08:10",      # 08:10（避开 AI 免费额度在北京时间 08:00 重置的边界）
    "/F",                # 已存在则覆盖
]

print("创建计划任务：", TASK_NAME)
result = subprocess.run(
    create_cmd, capture_output=True, text=True, encoding="gbk", errors="replace"
)
print(result.stdout.strip() or result.stderr.strip())
print("返回码：", result.returncode)

# 验证查询
check = subprocess.run(
    ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
    capture_output=True, text=True, encoding="gbk", errors="replace",
)
print("\n任务详情：")
print(check.stdout.strip() or check.stderr.strip())
