# examples/03_multi_tool_loop.py
# 目标：在 02_bash_loop.py 基础上，把单 bash 升级为 5 个工具的 dispatch 结构
# OpenAI 格式 + pydantic + 单一数据源 TOOL_REGISTRY
# 红线：不用 eval；每个 tool_call 必须 append role:tool；参数必须 pydantic 校验；
#       file 类工具必须过 safe_path；dispatch 用 registry.get() 兜底

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

# WORKDIR = Path(__file__).parent.resolve()
WORKDIR = Path.cwd()  # 改为当前工作目录，更通用

load_dotenv(override=True)

client = OpenAI(
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
)


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="the path of the file to read")
    limit: int | None = Field(None, description="maximum number of lines to read")


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="path to write the file to")
    content: str = Field(..., description="content to write into the file")


class EditFileArgs(BaseModel):
    path: str = Field(..., description="path to the file to edit")
    old_text: str = Field(..., description="exact text to find and replace")
    new_text: str = Field(..., description="text to replace the old_text with")


class GlobArgs(BaseModel):
    pattern: str = Field(..., description="glob pattern to search for files")


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
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


def run_read(path: str, limit: int | None = None) -> str:
    try:
        file_path = safe_path(path)
        lines = file_path.read_text().splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def run_glob(pattern: str) -> str:
    import glob as g

    try:
        results = []
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {str(e)}"


TOOL_REGISTRY = {
    "bash": ("Run a shell command.", BashArgs, run_bash),
    "read_file": ("Read file contents.", ReadFileArgs, run_read),
    "write_file": ("Write content to a file.", WriteFileArgs, run_write),
    "edit_file": ("Replace exact text in a file once.", EditFileArgs, run_edit),
    "glob": ("Find files matching a glob pattern.", GlobArgs, run_glob),
}

model = os.getenv("OPENROUTER_MODEL")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": args_model.model_json_schema(),
        },
    }
    for name, (desc, args_model, func) in TOOL_REGISTRY.items()
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
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            return msg.content

        for tc in msg.tool_calls:
            entry = TOOL_REGISTRY.get(tc.function.name)
            if not entry:
                result = f"Error: unknown tool '{tc.function.name}'"
            else:
                desc, ArgsModel, handler = entry
                try:
                    args = ArgsModel.model_validate_json(tc.function.arguments)
                except Exception as e:
                    result = f"Error: invalid arguments for tool '{tc.function.name}' - {str(e)}"
                else:
                    print(f"[tool call] {tc.function.name} with args: {args}")
                    result = handler(**args.model_dump())
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(
                f"[tool result] {str(result[:200] + '...') if len(result) > 200 else result}"
            )  # 只打印前200字符
    print("[已达最大轮次,中止]")


if __name__ == "__main__":
    print("03: Multi Tool Loop (5 tools via registry)")
    print("输入问题，回车发送。输入 q 退出。\n")

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
