# examples/02_bash_loop.py
# 目标：基于 01_minimal_loop.py 的结构，实现带 bash 工具的最小 agent loop
# 使用 OpenAI 兼容客户端（NVIDIA）
# 严格按照指定架构：pydantic 工具定义 + model_json_schema + model_validate_json + 安全 run_bash + 正确 append role:tool
# 红线：绝不用 eval；每个 tool_call 必须 append 一条 role:tool 消息；参数解析必须用 pydantic

import os
import subprocess

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
)


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


model = os.getenv("NVIDIA_MODEL")

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
    for trun in range(max_turns):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            return msg.content

        for tc in msg.tool_calls:
            args = BashArgs.model_validate_json(tc.function.arguments)
            print(f"[tool call] {tc.function.name} with args: {args}")
            result = run_bash(args.command)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result}")


if __name__ == "__main__":
    print("s01: Agent Loop")
    print("输入一个问题，回车发送。输入q退出。\n")

    messages = []
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)
