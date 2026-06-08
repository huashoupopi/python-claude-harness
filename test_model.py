import os

# 代理禁用必须在任何 import httpx/openai 之前
for var in list(os.environ.keys()):
    if "proxy" in var.lower():
        os.environ.pop(var, None)

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

http_client = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=2, max_connections=5),
    # 不再传 proxies（新版 httpx 已移除该参数）
)

client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
    http_client=http_client,
)

model = os.getenv("NVIDIA_MODEL")

print("Using model:", model)
print("Base URL:", os.getenv("NVIDIA_BASE_URL"))

# plain call
resp = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "hello"}],
    max_tokens=5,
)
print("Plain OK:", resp.choices[0].message.content)

# tool call
tools = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "return the input text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]

resp = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "use the echo tool to return 'hello'"}],
    tools=tools,
    tool_choice="auto",
)
print("Tool OK")
print(resp.choices[0].message)
