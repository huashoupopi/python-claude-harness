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
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
    timeout=120.0,
    default_headers={"User-Agent": "curl/8.7.1"},
)

WORKDIR = Path.cwd()
TASKS_DIR = WORKDIR / ".tasks"
TASKS_DIR.mkdir(exist_ok=True)
model = os.getenv("NVIDIA_MODEL")


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


def save_task(task: Task):
    _task_path(task.id).write_text(task.model_dump_json(indent=2))


def list_tasks() -> list[Task]:
    return [
        Task(**json.loads(p.read_text())) for p in sorted(TASKS_DIR.glob("task_*.json"))
    ]


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def get_task(task_id: str) -> str:
    """Return full task details as JSON."""
    task = load_task(task_id)
    return task.model_dump_json(indent=2)


def can_start(task_id: str) -> bool:
    """Check if all blockedBy dependencies are completed.
    Missing dependencies are treated as blocked."""
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != TaskStatus.COMPLETED:
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.PENDING:
        return f"Task {task_id} is {task.status}, cannot claim"
    if not can_start(task_id):
        deps = [
            d
            for d in task.blockedBy
            if not _task_path(d).exists() or load_task(d).status != TaskStatus.COMPLETED
        ]
        return f"Blocked by: {deps}"
    task.owner = owner
    task.status = TaskStatus.IN_PROGRESS
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress (owner: {owner})\033[0m")
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        return f"Task {task_id} is {task.status}, cannot complete"
    task.status = TaskStatus.COMPLETED
    save_task(task)
    unblocked = [
        t.subject
        for t in list_tasks()
        if t.status == TaskStatus.PENDING and t.blockedBy and can_start(t.id)
    ]
    print(f"  \033[32m[complete] {task.subject} ✓\033[0m")
    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
        print(f"  \033[33m[unblocked] {', '.join(unblocked)}\033[0m")
    return msg


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


class CreateTaskArgs(BaseModel):
    subject: str = Field(..., description="the subject of the task")
    description: str = Field("", description="the description of the task")
    blockedBy: list[str] | None = Field(
        None, description="list of task IDs that block this task"
    )


class TaskIdArgs(BaseModel):
    task_id: str = Field(..., description="the ID of the task to operate on")


class NoneArgs(BaseModel):
    pass


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
        "Claim a task by its ID, marking it as in_progress if possible.",
        TaskIdArgs,
        run_claim_task,
    ),
    "complete_task": (
        "Mark a task as completed by its ID, unblocking dependent tasks.",
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
            "parameters": args_model.model_json_schema(),
        },
    }
    for name, (desc, args_model, _) in TOOL_REGISTRY.items()
]

SYSTEM_PROMPT = (
    "You are a coding agent. Act, don't explain.\n"
    f"Available tools: {', '.join(TOOL_REGISTRY.keys())}.\n"
    f"Working directory: {WORKDIR}"
)


def agent_loop(messages: list):
    max_turns = 25
    for turn in range(max_turns):
        try:
            reps = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            messages.append(
                {"role": "assistant", "content": f"[Error] {type(e).__name__}: {e}"}
            )
            return
        msg = reps.choices[0].message
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
                    args = ArgsModel.model_validate_json(tc.function.arguments or "{}")
                except Exception as e:
                    print(f"Error: failed to parse tool arguments: {str(e)}")
                    result = f"Error: failed to parse tool arguments: {str(e)}"
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
            print(f"[tool result] {result[:200]}")
    print("达到最大轮次")


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    while True:
        user_input = input(">>> ")
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)
