"""
T15 · M5 白纸测试 ②  ——  任务系统(s12)全量手写

考场规则:
  - 闭卷。temp.py / demo4 / demo5 / 14_task_system.py / 15_background_tasks.py / 课程仓库全部关闭。
  - 逐字层豁免:import 路径、Field 写法、OpenAI 参数名、subprocess 参数名 —— 随便问,不扣分。
  - 卡住 2 次 → 要一句话方向,不要答案。
  - 不许贴代码求改写。
  - 目标:整链能跑 —— 模型能建任务、列任务、查任务、认领、完成并看到解锁。

下面只有零件名和顺序,没有签名、没有逻辑。往下填。
"""

# ── 1. 常量区 ────────────────────────────────────────────
#   imports / load_dotenv / client / model / WORKDIR / TASKS_DIR(记得 mkdir)


# ── 2. 数据模型 ──────────────────────────────────────────
#   TaskStatus
#   Task


# ── 3. 存取件 ────────────────────────────────────────────
#   _task_path
#   create_task
#   save_task
#   load_task
#   list_tasks
#   get_task


# ── 4. 业务件 ────────────────────────────────────────────
#   can_start
#   claim_task
#   complete_task


# ── 5. 工具参数模型 ──────────────────────────────────────
#   BashArgs
#   CreateTaskArgs
#   TaskIdArgs
#   NoneArgs


# ── 6. handler 薄壳 ──────────────────────────────────────
#   run_bash
#   run_create_task
#   run_list_tasks
#   run_get_task
#   run_claim_task
#   run_complete_task


# ── 7. 工具注册与 schema ─────────────────────────────────
#   TOOL_REGISTRY
#   TOOLS


# ── 8. system prompt ─────────────────────────────────────
#   SYSTEM_PROMPT(一段写死的字符串即可,不做 s10 的组装/缓存)


# ── 9. 主循环 ────────────────────────────────────────────
#   agent_loop

# ── 10. REPL ─────────────────────────────────────────────
#   if __name__ == "__main__":
import json
import os
import random
import subprocess
import time
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv(override=True)

client = OpenAI(
    base_url=os.environ.get("NVIDIA_BASE_URL"),
    api_key=os.environ.get("NVIDIA_API_KEY"),
    timeout=120.0,
    default_headers={"User-Agent": "curl/8.7.1"},
)

model = os.environ.get("NVIDIA_MODEL")
WORKDIR = Path.cwd()
TASKS_DIR = WORKDIR / ".tasks"
TASKS_DIR.mkdir(exist_ok=True)


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Task(BaseModel):
    id: str = Field(..., description="Unique identifier for the task")
    subject: str = Field(..., description="Subject or title of the task")
    description: str = Field(..., description="Description of the task")
    status: TaskStatus = Field(TaskStatus.PENDING, description="Status of the task")
    owner: str | None = Field(None, description="Owner of the task")
    blockedBy: list[str] = Field(
        default_factory=list, description="List of task IDs that block this task"
    )


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def create_task(
    subject: str, description: str = "", blockedBy: list[str] | None = None
) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject,
        description=description,
        status=TaskStatus.PENDING,
        owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def save_task(task: Task) -> None:
    _task_path(task.id).write_text(task.model_dump_json(indent=2))


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def list_tasks() -> list[Task]:
    return [
        Task(**json.loads(p.read_text())) for p in sorted(TASKS_DIR.glob("task_*.json"))
    ]


def get_task(task_id: str) -> str:
    task = load_task(task_id)
    return task.model_dump_json(indent=2)


def can_start(task: Task) -> bool:
    for blocked_id in task.blockedBy:
        if not _task_path(blocked_id).exists():
            return False
        if load_task(blocked_id).status != TaskStatus.COMPLETED:
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.PENDING:
        return f"Error: Task {task_id} is not pending and cannot be claimed."
    if not can_start(task):
        return f"Error: Task {task_id} has unmet dependencies and cannot be claimed."
    task.status = TaskStatus.IN_PROGRESS
    task.owner = owner
    save_task(task)
    return f"Task {task_id} claimed by {owner}."


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        return f"Error: Task {task_id} is not in progress and cannot be completed."
    task.status = TaskStatus.COMPLETED
    save_task(task)
    unblocked = []
    for t in list_tasks():
        if t.status == TaskStatus.PENDING and t.blockedBy and can_start(t):
            unblocked.append(t.id)
    return f"Task {task_id} marked as completed. Unblocked tasks: {', '.join(unblocked) if unblocked else 'None'}."


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


def run_create_task(
    subject: str, description: str = "", blockedBy: list[str] | None = None
) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks. Use create_task to add some."
    lines = []
    for t in tasks:
        icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
        deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
        owner = f" [{t.owner}]" if t.owner else ""
        lines.append(f"  {icon} {t.id}: {t.subject} [{t.status}]{owner}{deps}")
    return "\n".join(lines)


def run_get_task(task_id: str) -> str:
    try:
        return get_task(task_id)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"


def run_claim_task(task_id: str) -> str:
    return claim_task(task_id, owner="agent")


def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


class CreateTaskArgs(BaseModel):
    subject: str = Field(..., description="the subject of the task")
    description: str = Field("", description="the description of the task")
    blockedBy: list[str] | None = Field(
        None, description="list of task IDs that block this task"
    )


class TaskIdArgs(BaseModel):
    task_id: str = Field(..., description="the ID of the task")


class NoneArgs(BaseModel):
    pass


TOOL_REGISTRY = {
    "bash": ("Run a shell command.", BashArgs, run_bash),
    "create_task": (
        "Create a new task with subject, description, and optional blockedBy list.",
        CreateTaskArgs,
        run_create_task,
    ),
    "list_tasks": (
        "List all tasks with their status and dependencies.",
        NoneArgs,
        run_list_tasks,
    ),
    "get_task": (
        "Get full details of a task by its ID.",
        TaskIdArgs,
        run_get_task,
    ),
    "claim_task": (
        "Claim a task by its ID, marking it as in progress.",
        TaskIdArgs,
        run_claim_task,
    ),
    "complete_task": (
        "Complete a task by its ID.",
        TaskIdArgs,
        run_complete_task,
    ),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": args.model_json_schema(),
        },
    }
    for name, (desc, args, _) in TOOL_REGISTRY.items()
]

SYSTEM_PROMPT = (
    "You are a coding agent. Act, don't explain.\n"
    f"Available tools: {', '.join(TOOL_REGISTRY.keys())}.\n"
    f"Working directory: {WORKDIR}"
)


def agent_loop(messages: list):
    max_turns = 25
    for turn in range(max_turns):
        reps = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = reps.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            return msg.content
        for tc in msg.tool_calls:
            if tc.function.name not in TOOL_REGISTRY:
                result = f"Error: unknown tool {tc.function.name}"
            else:
                entry = TOOL_REGISTRY[tc.function.name]
                desc, args_model, handler = entry
                try:
                    args = args_model.model_validate_json(tc.function.arguments or "{}")
                except Exception as e:
                    print(f"Error: failed to parse tool arguments: {str(e)}")
                    result = f"Error: failed to parse tool arguments: {str(e)}"
                else:
                    result = handler(**args.model_dump())
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result[:200]}")
    print("达到最大轮次")


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    while True:
        user_input = input(">>> ")
        if user_input.lower() == "q":
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)
