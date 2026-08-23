"""创建 Windows 计划任务：每天 08:00 运行 run_daily.py 并推送微信早报。

用法：python setup_schedule.py
说明：任务以当前用户身份创建，仅在用户登录时运行（可继承你的环境变量，
      如 OPENAI_API_KEY）。如需"未登录也运行"，请用任务计划程序 GUI 修改。
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
    "/ST", "08:00",      # 08:00
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
