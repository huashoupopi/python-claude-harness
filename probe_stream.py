import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)
client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
)
model = os.getenv("NVIDIA_MODEL")


def accumulate_stream(chunks):
    final_tool_calls = {}
    final_text = ""
    finish_reason = None
    for chunk in chunks:
        delta = chunk.choices[0].delta
        if delta.content:
            final_text += delta.content
            print(delta.content, end="", flush=True)
        for tool_call in delta.tool_calls or []:
            index = tool_call.index
            if index not in final_tool_calls:
                final_tool_calls[index] = tool_call
            else:
                final_tool_calls[
                    index
                ].function.arguments += tool_call.function.arguments
        if chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

    return final_text, final_tool_calls, finish_reason


def build_message(text, tool_calls):
    """把累积结果拼成一条 assistant 消息。

    形状必须与非流式的 msg.model_dump(exclude_none=True) 一致,
    否则塞回 messages 后下一轮请求会 400。
    """
    msg = {"role": "assistant", "content": text}

    # ↓↓↓ 你写这段 ↓↓↓
    # 如果 tool_calls 非空:
    #   ① 把字典的值【按 key 排序】取出来
    #   ② 每个都 .model_dump(exclude={"index"})
    #   ③ 结果塞进 msg["tool_calls"]
    if tool_calls:
        msg["tool_calls"] = [
            call.model_dump(exclude={"index"}) for _, call in sorted(tool_calls.items())
        ]
    # ↑↑↑ 你写这段 ↑↑↑

    return msg


if __name__ == "__main__":
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current temperature for a given location.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City and country e.g. Bogotá, Colombia",
                        }
                    },
                    "required": ["location"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "你好！"}],
        tools=tools,
        stream=True,
    )
    text, tool_calls, reason = accumulate_stream(stream)
    print(build_message(text, tool_calls))
