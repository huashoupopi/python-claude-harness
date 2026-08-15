"""流式累积逻辑的单元测试。被测:**22_trunk.accumulate_stream**(经 conftest 的 trunk fixture)

🔴 2026-08-15 换靶子:本文件原来 `from probe_stream import accumulate_stream`——
那是 T20 探针脚本里的【另一份副本】。今天给主干加 usage(返回值 3 元组→4 元组)时,
这 7 条测试【一声都没吭】,79 条全绿 —— 因为它们守的根本不是主干。
两份已经分叉:probe_stream 那份停在 8/12,没有 usage、没有空 choices 保护。
🪝 测试文件里的 import 写错对象,测试就变成纯装饰品 —— 而且它永远是绿的,永远不提醒你。
🪝 同一个病的第二次:T18.5 冒烟闸立项的原因正是「12 条绿灯测的是 harness/ 死包」。
   这笔账 T20 结站时自己记过(「两处各存一份,T19 收进包时统一」),没还,今天暴雷。

为什么必须造假数据:真端点(deepseek-v4-flash)的 arguments 是【一片给完】的,
按 index 累积那条路径在真实调用里【永远跑不到】——改对了也证明不了。
只能用假 chunk 喂进去,才能把 OpenAI 官方那种分片形状测出来。

四种形状对应四条测试:
  D 纯文本不调工具            (已写,当样板)
  A 工具调用一片给完          (本端点真实行为)
  C 工具调用 arguments 分 8 片 (OpenAI 官方行为,本文件的全部意义)
  B 工具调用与 finish_reason 挤在同一片(本端点第一次实测行为)
"""

import json

from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall


def make_chunk(content=None, tool_calls=None, finish_reason=None):
    """造一个假 chunk。

    tool_calls 传 list[dict],每项形如:
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"'}}
    非首片通常 id / type / function.name 都是 None,只有 arguments 有值。
    """
    return ChatCompletionChunk.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": finish_reason,
                }
            ],
        }
    )


def test_text_only_no_tool_calls(trunk):
    """形状D:纯说话不调工具。文本拼全、工具篮子为空、finish_reason 是 stop。"""
    chunks = [
        make_chunk(content="你好"),
        make_chunk(content="!"),
        make_chunk(finish_reason="stop"),
    ]
    text, tool_calls, reason, _usage = trunk.accumulate_stream(chunks)
    assert text == "你好!"
    assert tool_calls == {}
    assert reason == "stop"


# ---------------------------------------------------------------------------
# 下面三条是你的活
# ---------------------------------------------------------------------------


# TODO ① 形状A:工具调用一片给完(本端点真实行为)
#   数据:片1 content='\n\n'
#        片2 tool_calls=[{"index":0, "id":"call_1", "type":"function",
#                         "function":{"name":"get_weather",
#                                     "arguments":'{"location":"New York, USA"}'}}]
#        片3 finish_reason='tool_calls'
#   断言:篮子里 1 个;arguments 能被 json.loads 吃下且 location 对;
#        name/id 对;reason == 'tool_calls'
def test_single_chunk_tool_call(trunk):
    chunks = [
        make_chunk(content="\n\n"),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location":"New York, USA"}',
                    },
                }
            ]
        ),
        make_chunk(finish_reason="tool_calls"),
    ]
    text, tool_calls, reason, _usage = trunk.accumulate_stream(chunks)
    assert text == "\n\n"
    assert len(tool_calls) == 1
    call = tool_calls[0]
    args = json.loads(call.function.arguments)
    assert args["location"] == "New York, USA"
    assert call.function.name == "get_weather"
    assert call.id == "call_1"
    assert reason == "tool_calls"


# TODO ② 形状C:arguments 分 8 片 🔴 本文件的全部意义
#   数据:第 1 片带 id/name/type 且 arguments=""
#        后 7 片 id/type/function.name 全为 None,只有 arguments 分片:
#        ""  '{"'  'location'  '":"'  'Paris'  ','  ' France'  '"}'
#        最后再来一片 finish_reason='tool_calls'
#   断言:拼出来 == '{"location":"Paris, France"}';json.loads 后 location 对;
#        name/id 没丢(它们只在第一片出现过)
#   ⚠️ 这条红了就说明 .function.arguments 那行或 if/else 有问题
def test_chunked_arguments_are_accumulated(trunk):
    chunks = [
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": ""},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": '{"'},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": "location"},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": '":"'},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": "Paris"},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": ","},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": " France"},
                }
            ]
        ),
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": '"}'},
                }
            ]
        ),
        make_chunk(finish_reason="tool_calls"),
    ]
    text, tool_calls, reason, _usage = trunk.accumulate_stream(chunks)
    assert text == ""
    assert len(tool_calls) == 1
    call = tool_calls[0]
    args = json.loads(call.function.arguments)
    assert args["location"] == "Paris, France"
    assert call.function.name == "get_weather"
    assert call.id == "call_1"


# TODO ③ 形状B:工具调用与 finish_reason 挤在同一片
#   数据:最后一片【同时】带 tool_calls 和 finish_reason='tool_calls'
#   断言:工具调用没丢
#   说明:你现在没写 break,所以应该直接绿——这条是防止未来改坏的护栏
def test_tool_call_in_the_same_chunk_as_finish_reason(trunk):
    chunks = [
        make_chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location":"Tokyo, Japan"}',
                    },
                }
            ],
            finish_reason="tool_calls",
        )
    ]
    text, tool_calls, reason, _usage = trunk.accumulate_stream(chunks)
    assert len(tool_calls) == 1
    call = tool_calls[0]
    args = json.loads(call.function.arguments)
    assert args["location"] == "Tokyo, Japan"
    assert call.function.name == "get_weather"
    assert call.id == "call_1"
    assert reason == "tool_calls"


"""
① 有工具调用
   输入:text + 一个 {0: ChoiceDeltaToolCall(...)} 字典
   断言:msg == 目标 dict（整个比,不用逐字段）

② 纯文本
   输入:text + 空字典 {}
   断言:"tool_calls" not in msg

③ 并行两个工具,乱序进来 🔴
   输入:{1: 对象B, 0: 对象A}   ← 故意把 1 写在前面
   断言:输出的 tool_calls 顺序是 [A, B]
"""


def test_has_tool_call(trunk):
    text = "Hello"
    tool_calls = {
        0: ChoiceDeltaToolCall.model_validate(
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"New York, USA"}',
                },
            }
        )
    }
    msg = trunk.build_message(text, tool_calls)
    expected_msg = {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"New York, USA"}',
                },
            }
        ],
    }
    assert msg == expected_msg


def test_no_tool_call(trunk):
    text = "Hello"
    tool_calls = {}
    msg = trunk.build_message(text, tool_calls)
    assert "tool_calls" not in msg


def test_parallel_tool_calls_out_of_order(trunk):
    text = "Hello"
    tool_calls = {
        1: ChoiceDeltaToolCall.model_validate(
            {
                "index": 1,
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"Los Angeles, USA"}',
                },
            }
        ),
        0: ChoiceDeltaToolCall.model_validate(
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"New York, USA"}',
                },
            }
        ),
    }
    msg = trunk.build_message(text, tool_calls)
    expected_msg = {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"New York, USA"}',
                },
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"Los Angeles, USA"}',
                },
            },
        ],
    }
    assert msg == expected_msg


def make_usage_chunk(prompt=10, completion=2):
    """造一片【只有 usage、没有 choices】的 chunk。

    这是 stream_options={"include_usage": True} 时真实会来的最后一片。
    make_chunk 造不出它 —— 那个函数固定塞一个 choice 进去。
    """
    return ChatCompletionChunk.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fake-model",
            "choices": [],  # ← 空!
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
    )


def test_usage_chunk_has_empty_choices(trunk):
    """🔴 形状E:usage 那一片 choices 是空列表 —— chunk.choices[0] 会 IndexError。

    2026-08-15 真端点实测(scratchpad/probe_usage.py):
        片 0  choices=1  content='hi'   usage=None
        片 1  choices=1  finish=stop    usage=None
        片 2  choices=0  ← 空!          usage=CompletionUsage(prompt=10, completion=2)

    两条都要钉住:
      ① 空 choices 不许炸(必须先判断再取 [0])
      ② usage 片在 finish_reason 【之后】才来 —— 它不能把已收到的 finish_reason 冲掉,
         而且「看到 finish 就 break」的写法会永远丢掉 usage。
    🪝 又一次「不能假设分片的形状」—— T20 学的是分片【边界】不确定,
       这次是分片【结构】本身都会变。
    """
    chunks = [
        make_chunk(content="hi"),
        make_chunk(finish_reason="stop"),
        make_usage_chunk(prompt=10, completion=2),
    ]
    text, tool_calls, reason, usage = trunk.accumulate_stream(chunks)
    assert text == "hi"
    assert tool_calls == {}
    assert reason == "stop", "usage 片不该把 finish_reason 冲掉"
    assert usage is not None, "usage 没收到 —— 检查 continue 是不是放在收 usage 之前了"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (10, 2, 12)


def test_no_usage_when_not_requested(trunk):
    """不带 include_usage 时(＝流里没有 usage 片),usage 应为 None 而不是报错。

    对照组。A 组探针实测:不要就永远不给,全程 usage=None。
    """
    chunks = [make_chunk(content="hi"), make_chunk(finish_reason="stop")]
    _text, _tc, _reason, usage = trunk.accumulate_stream(chunks)
    assert usage is None
