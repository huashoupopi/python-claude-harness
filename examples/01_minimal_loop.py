"""
Phase 0 / s01 对标：最小 agent loop（独立项目版）

使用 OpenAI 客户端（因为优先 NVIDIA，是 OpenAI 兼容格式）。
真实 CC 的核心也是这个形状：while True 发送消息，检查 tool call，执行后把结果 append 回去。

参考真实 CC 恢复源码（pengchengneo/Claude-Code 等）：
- query loop 基本是 while needs_follow_up
- tool 执行有并行安全判断
- 这里我们先做最简顺序版 + 固定一个 tool

运行：
uv run python examples/01_minimal_loop.py
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 用你的 NVIDIA（OpenAI 格式）
client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
)

MODEL = os.getenv("NVIDIA_MODEL", "deepseek-v4-flash")

def echo(text: str) -> str:
    """简单 tool：把输入原样返回"""
    return text

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "把输入的 text 原样返回",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"}
                },
                "required": ["text"],
            },
        },
    }
]

TOOL_HANDLERS = {
    "echo": echo,
}

def run_minimal_loop(user_prompt: str):
    messages = [{"role": "user", "content": user_prompt}]

    print(f"[user] {user_prompt}")

    for _ in range(10):  # 安全上限
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_msg = response.choices[0].message
        messages.append(assistant_msg)

        if not assistant_msg.tool_calls:
            print(f"[assistant] {assistant_msg.content}")
            return assistant_msg.content

        # 执行 tool（顺序执行，最简版）
        for tool_call in assistant_msg.tool_calls:
            name = tool_call.function.name
            args = eval(tool_call.function.arguments)  # 实际项目要用 json.loads + 验证

            print(f"[tool call] {name}({args})")

            if name in TOOL_HANDLERS:
                result = TOOL_HANDLERS[name](**args)
            else:
                result = f"未知 tool: {name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": name,
                "content": str(result),
            })

            print(f"[tool result] {result}")

    return "达到最大轮次"

if __name__ == "__main__":
    run_minimal_loop("请用 echo 工具返回 'hello from loop'")
