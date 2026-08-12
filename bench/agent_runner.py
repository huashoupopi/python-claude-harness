import importlib.util
import sys
from pathlib import Path

# SRC = "/Users/liuchenxu/Documents/Documents/code/claude-code-harness-study/python-claude-harness/examples/11_memory_tool.py"
SRC = Path(
    "/Users/liuchenxu/Documents/Documents/code/claude-code-harness-study/python-claude-harness/examples"
) / Path(sys.argv[2])
WORKDIR = Path.cwd()
SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."
# 三行咒语:按文件路径装载模块(live 驱动脚本里你见过它)
spec = importlib.util.spec_from_file_location("harness", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 你的部分:①从 sys.argv 拿 task.md 路径,读出文字
#          ②拼 messages:一条 system(用 m.build_system())+一条 user(考题文字)
#          ③调 m.agent_loop(messages)
content = Path(sys.argv[1]).read_text()
if not hasattr(m, "build_system"):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": content},
    ]
else:
    messages = [
        {"role": "system", "content": m.build_system()},
        {"role": "user", "content": content},
    ]
m.agent_loop(messages)
