"""
Provider Adapter 示例 - OpenAI 兼容格式（NVIDIA NIM 等）

真实 CC 的 query 层会把不同 provider 的响应统一成内部的 tool use 表示。
我们这里先做一个最简的 adapter，把 OpenAI 的 tool_calls 转成我们后面会用的统一格式。

这个设计参考了真实 CC 恢复源码中对不同后端的抽象思路（pengchengneo/Claude-Code 等）。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import httpx
from openai import OpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: List[ToolCall]


class OpenAIProvider:
    """
    封装 OpenAI 兼容客户端（NVIDIA、DeepSeek OpenAI 模式等）
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=httpx.Client(timeout=timeout),
        )

    def create_message(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        发送请求，返回统一格式的 LLMResponse
        """
        if system:
            # OpenAI 格式把 system 放在 messages 里
            full_messages = [{"role": "system", "content": system}] + messages
        else:
            full_messages = messages

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        tool_calls: List[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                import json
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
        )

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> Dict[str, Any]:
        """
        把 tool 执行结果转成发回模型的消息格式（OpenAI 格式）
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": result,
        }
