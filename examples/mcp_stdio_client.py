"""真 MCP client（stdio）—— T17 周末动手件。

先在这个独立文件里练通，再合进 21_mcp.py 的 MCPClient。

【已给你】Popen 开法、类骨架、_rpc 的收发机械部分、底部自测
【要你填】四处 TODO —— 那四处是这一步的全部价值，别跳过
"""

import json
import subprocess
import sys


class MCPStdioClient:
    """通过 stdio 连接一个真实 MCP server 的客户端。"""

    def __init__(self, name: str, command: list[str]):
        """
        name    —— 本地注册名（做工具前缀用，不要用 server 自报的 serverInfo.name）
        command —— 启动命令，例如 ["python", "toy_mcp_server.py"]
        """
        self.name = name
        self.tools: list[dict] = []  # tools/list 拿回来的工具定义存这里
        self._next_id = 0  # 自增请求号

        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,  # 我往它输入里写
            stdout=subprocess.PIPE,  # 我读它的输出
            stderr=None,  # 调试期：让 server 日志直接打到终端
            text=True,  # 收发 str 而不是 bytes
            bufsize=1,  # 行缓冲
        )

    # ────────────────────────────────────────────────────────────
    # 心脏：发一条请求，等一条回应
    # ────────────────────────────────────────────────────────────
    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        req_id = self._next_id

        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "my-harness",
                        "version": "0.1.0",
                    },
                },
            },
        }

        # 发
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

        # 收
        line = self.proc.stdout.readline()
        if not line:  # 空字符串 = 对面已经关了（进程死了）
            raise RuntimeError(f"MCP server '{self.name}' 断开了（stdout 读到 EOF）")
        resp = json.loads(line)

        # ── TODO ① 收到的 id 和发出去的对得上吗？──────────────────
        # 想清楚：如果对不上，说明发生了什么？该怎么处理？
        # （提示：这是你第三次见 id 配对——s07 tool_call_id、s16 request_id、现在）
        #
        # 你的代码写这里
        if resp.get("id") != req_id:
            raise RuntimeError(
                f"MCP server '{self.name}' 返回的 id={resp.get('id')} 和请求 id={req_id} 不匹配"
            )

        # ── TODO ② 错误分流 ──────────────────────────────────────
        # resp 里可能是 "result"，也可能是 "error"。
        # 想清楚：
        #   - JSON-RPC 层的 error（-32601 方法不存在 / -32602 参数非法）
        #     ——这是【你的代码写错了】，该让它安静地返回，还是大声地炸？
        #   - 注意：工具执行失败【不在这里】，它在 result 里带 isError=true
        #
        # 你的代码写这里
        if "error" in resp:
            error = resp["error"]
            raise RuntimeError(
                f"MCP server '{self.name}' 返回错误: code={error.get('code')}, message={error.get('message')}"
            )

        return resp["result"]

    # ────────────────────────────────────────────────────────────
    # 发现：问 server 有哪些工具
    # ────────────────────────────────────────────────────────────
    def register(self) -> list[dict]:
        # ── TODO ③ 发 tools/list，把结果存进 self.tools ────────────
        # 想清楚：
        #   - 存原样的 list[dict]，还是转成别的形状？
        #   - 21_mcp.py 里 assemble_tool_pool 会怎么用它？
        #     （它现在读的是 tool_def["name"] 和 tool_def["inputSchema"]）
        #   - ⚠️ 真 server 给的 inputSchema 是 dict，而 21_mcp.py 里
        #     assemble_tools() 写的是 args_model.model_json_schema()
        #     —— 那笔债在这里撞上，但【先别改 21_mcp.py】，本文件跑通再说
        #
        # 你的代码写这里
        result = self._rpc("tools/list")
        self.tools = result.get("tools", [])
        return self.tools

    # ────────────────────────────────────────────────────────────
    # 调用：让 server 执行一个工具
    # ────────────────────────────────────────────────────────────
    def call_tool(self, tool_name: str, args: dict) -> str:
        # ── TODO ④ 发 tools/call，把返回的 content 转成【字符串】────
        # 想清楚：
        #   - server 回的 content 是【数组】，每项形如 {"type":"text","text":"..."}
        #     可能有多项，也可能有非 text 类型（image/audio/resource_link）
        #   - 你的 handler 契约是「返回给模型看的字符串」——薄壳层翻译官（s12）
        #   - result 里还有 isError 字段。为 true 时怎么办？
        #     （提示：spec 说 client SHOULD 把它给模型，让模型自我纠正）
        #
        # 你的代码写这里
        result = self._rpc("tools/call", {"name": tool_name, "arguments": args})
        results = []
        content = result.get("content", [])
        is_error = result.get("isError", False)
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                results.append(text)
            else:
                results.append(f"[非文本内容，type={item.get('type')}]")
        output = "\n".join(results)
        if is_error:
            output = f"[工具执行失败]\n{output}"
        return output

    # ────────────────────────────────────────────────────────────
    # 关闭：spec 的三步 —— 关 stdin → 等退出 → 超时强杀
    # ────────────────────────────────────────────────────────────
    def close(self):
        if self.proc.poll() is not None:  # 已经死了
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# ════════════════════════════════════════════════════════════════
# 自测：四处 TODO 填完之后，直接 python mcp_stdio_client.py 跑这个
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    client = MCPStdioClient("docs", [sys.executable, "toy_mcp_server.py"])
    try:
        print("\n── ① 握手 ──")
        info = client._rpc("server/discover")
        print(f"  支持版本: {info.get('supportedVersions')}")
        print(f"  能力:     {list(info.get('capabilities', {}).keys())}")

        print("\n── ② 发现工具 ──")
        tools = client.register()
        for t in tools:
            print(f"  {t['name']}: {t.get('description', '')}")
        print(f"  self.tools 存了 {len(client.tools)} 个")

        print("\n── ③ 调用工具 ──")
        out = client.call_tool("get_weather", {"location": "Hangzhou"})
        print(f"  返回类型: {type(out).__name__}   ← 必须是 str")
        print(f"  返回内容: {out}")

        print("\n── ④ 调一个不存在的工具 ──")
        try:
            out = client.call_tool("rm_rf", {})
            print(f"  返回: {out}")
        except Exception as e:
            print(f"  抛异常: {type(e).__name__}: {e}")
        print("  ↑ 抛异常还是返回字符串？你在 TODO ② 的决定决定了这里")

    finally:
        print("\n── 关闭 ──")
        client.close()
        print(f"  退出码: {client.proc.returncode}   ← 0 表示 server 自己优雅退出了")
