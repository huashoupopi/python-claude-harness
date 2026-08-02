"""M5 白纸测试 ① —— 正式考卷(开考 2026-07-22 22:47,通过 23:16,8.5/10,监考:Archon)

【目标】最小 agent loop:模型调用一个真工具(bash),拿到结果后给出最终回答。
【必考六件】system+messages / while+保险丝 / 调 API 检测工具调用 /
            配对回填 / 无调用则输出退出 / 一个工具带 schema
【规则】不开旧代码不开课程仓库;逐字层豁免(包名/import/字段拼写可直接问);
        卡死协议(卡超 10 分钟就说,提示记录在案);及格线=验收任务全链跑通。
【验收任务】跑起来后输入:「当前目录下最大的 .py 文件是哪个?多少字节?」

——以下是答题区,从这里开始写——
"""

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv(override=True)

client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
    timeout=120.0,
    default_headers={"User-Agent": "curl/8.7.1"},
)

WORKDIR = Path.cwd()
SYSTEM = f"You are a coing agent at {WORKDIR}, you can run bash commands to help answer questions."
model = os.getenv("NVIDIA_MODEL")


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            output = "(no output)"
        return output[:50000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {str(e)}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": BashArgs.model_json_schema(),
        },
    }
]


def agent_loop(messages):
    max_turns = 25
    for turn in range(max_turns):
        reps = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = reps.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            return msg.content
        for tc in msg.tool_calls:
            if tc.function.name != "bash":
                result = f"Error: unknown tool {tc.function.name}"
            else:
                try:
                    args = BashArgs.model_validate_json(tc.function.arguments)
                except Exception as e:
                    print(f"Error: failed to parse tool arguments: {str(e)}")
                    result = f"Error: failed to parse tool arguments: {str(e)}"
                else:
                    result = run_bash(args.command)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result[:200]}")
    print("达到最大轮次")


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        user_input = input(">>> ")
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)
