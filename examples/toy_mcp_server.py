import json
import sys


def log(msg):
    """所有日志走 stderr —— 往 stdout 写一个字都会炸协议"""
    print(f"[server] {msg}", file=sys.stderr, flush=True)


def send(payload):
    """往 stdout 写一条消息。三个要点都在这三行里："""
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False) + "\n"
    )  # ① 末尾加 \n（json.dumps 不加）
    sys.stdout.flush()  # ② 必须 flush


log("启动，等 stdin")

for line in sys.stdin:
    line = line.strip()
    if not line:  # 空行跳过（灌数据时容易多个空行）
        continue

    req = json.loads(line)  # ← 收：一行文本 → Python 字典
    method = req.get("method")
    req_id = req.get("id")
    log(f"收到 method={method} id={req_id}")
    if req_id is None:
        continue
    if method == "server/discover":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,  # ③ id 原样带回去 —— 这是配对的唯一依据
                "result": {
                    "resultType": "complete",
                    "supportedVersions": ["2026-07-28"],
                    "capabilities": {"tools": {}, "resources": {}},
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "ExampleServer",
                            "version": "1.0.0",
                        }
                    },
                    "instructions": "This server provides weather and resource utilities.",
                    "ttlMs": 3600000,
                    "cacheScope": "public",
                },
            }
        )
    elif method == "tools/list":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "tools": [
                        {
                            "name": "get_weather",
                            "title": "Weather Information Provider",
                            "description": "Get current weather information for a location",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "location": {
                                        "type": "string",
                                        "description": "City name or zip code",
                                    }
                                },
                                "required": ["location"],
                            },
                        }
                    ],
                    "ttlMs": 300000,
                    "cacheScope": "public",
                },
            }
        )
    elif method == "tools/call":
        request_params = req.get("params", {})
        tool_name = request_params.get("name")
        tool_args = request_params.get("arguments", {})
        if tool_name != "get_weather":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Tool '{tool_name}' not found",
                    },
                }
            )
            continue
        location = tool_args.get("location", "Unknown")
        result = f"The weather in {location} is sunny with a temperature of 25°C."
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "content": [
                        {
                            "type": "text",
                            "text": result,
                        }
                    ],
                    "isError": False,
                },
            }
        )
    else:
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": "function not found",
                },
            }
        )

log("stdin 关了，退出")
