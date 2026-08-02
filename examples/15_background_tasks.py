import json
import os
import random
import subprocess
import threading
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
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_DIR.mkdir(exist_ok=True)
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
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
    run_in_background: bool = Field(
        False, description="whether to run the command in the background"
    )


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


def run_bash(command: str, run_in_background: bool = False) -> str:
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

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": f"Available tools: {', '.join(TOOL_REGISTRY.keys())}.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    """Select and join prompt sections based on current context."""
    sections = []

    # Always loaded — identity, tools, workspace
    sections.append(PROMPT_SECTIONS["identity"])
    sections.append(PROMPT_SECTIONS["tools"])
    sections.append(PROMPT_SECTIONS["workspace"])

    # Conditional — memory loaded when MEMORY.md exists and has content
    memories = context.get("memories", "")
    if memories:
        sections.append(f"Relevant memories:\n{memories}")

    return "\n\n".join(sections)


_last_context_key = None
_last_prompt = None


def get_system_prompt(context: dict) -> str:
    """Cache wrapper — reassemble only when context changes.

    Uses json.dumps for deterministic serialization, not Python's hash()
    which has process randomization and fails on nested dicts/lists.
    This cache only avoids redundant string assembly within a process.
    Real Claude Code additionally protects API-level prompt cache via
    stable section ordering and SYSTEM_PROMPT_DYNAMIC_BOUNDARY.
    """
    global _last_context_key, _last_prompt
    key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)
    if key == _last_context_key and _last_prompt:
        print("  \033[90m[cache hit] system prompt unchanged\033[0m")
        return _last_prompt
    _last_context_key = key
    _last_prompt = assemble_system_prompt(context)

    loaded = ["identity", "tools", "workspace"]
    if context.get("memories"):
        loaded.append("memory")
    print(f"  \033[32m[assembled] sections: {', '.join(loaded)}\033[0m")
    return _last_prompt


def update_context(context: dict, messages: list) -> dict:
    """Derive context from real state: which tools exist, whether memory files exist."""
    memories = ""
    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_text().strip()
        if content:
            memories = content
    return {
        "enabled_tools": list(TOOL_REGISTRY.keys()),  # list(TOOL_HANDLER.keys())
        "workspace": str(WORKDIR),
        "memories": memories,
    }


_bg_counter = 0
background_tasks: dict[str, dict] = {}  # bg_id → {tool_use_id, command, status}
background_results: dict[str, str] = {}  # bg_id → output
background_lock = threading.Lock()


def is_slow_operation(tc_name: str, tc_args: dict) -> bool:
    """Fallback heuristic: commands likely to take > 30s."""
    if tc_name != "bash":
        return False
    cmd = tc_args.get("command", "").lower()
    slow_keywords = [
        "install",
        "build",
        "test",
        "deploy",
        "compile",
        "docker build",
        "pip install",
        "npm install",
        "cargo build",
        "pytest",
        "make",
    ]
    return any(kw in cmd for kw in slow_keywords)


def should_run_background(tc_name: str, tc_args: dict) -> bool:
    """Model explicit request takes priority; fallback to heuristic."""
    if tc_args.get("run_in_background"):
        return True
    return is_slow_operation(tc_name, tc_args)


def execute_tool(tc) -> str:
    """Execute a tool call block, return output."""
    entry = TOOL_REGISTRY.get(tc.function.name)
    if not entry:
        return f"Error: unknown tool '{tc.function.name}'"
    else:
        desc, ArgsModel, handler = entry
        try:
            args = ArgsModel.model_validate_json(tc.function.arguments or "{}")
        except Exception as e:
            return f"Error: invalid arguments for tool '{tc.function.name}' - {str(e)}"
        else:
            print(f"[tool call] {tc.function.name} with args: {args}")
            return handler(**args.model_dump())


def start_background_task(tc, args) -> str:
    """Run tool in a daemon thread. Returns background task ID."""
    global _bg_counter
    _bg_counter += 1
    bg_id = f"bg_{_bg_counter:04d}"
    cmd = args.get("command", tc.function.name)

    def worker():
        result = execute_tool(tc)
        with background_lock:
            background_tasks[bg_id]["status"] = "completed"
            background_results[bg_id] = result

    with background_lock:
        background_tasks[bg_id] = {
            "tool_use_id": tc.id,
            "command": cmd,
            "status": "running",
        }
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(f"  \033[33m[background] dispatched {bg_id}: {cmd[:40]}\033[0m")
    return bg_id


def collect_background_results() -> list[str]:
    """Collect completed background results as task_notification messages."""
    with background_lock:
        ready_ids = [
            bid
            for bid, task in background_tasks.items()
            if task["status"] == "completed"
        ]
    notifications = []
    for bg_id in ready_ids:
        with background_lock:
            task = background_tasks.pop(bg_id)
            output = background_results.pop(bg_id, "")
        summary = output[:200] if len(output) > 200 else output
        notifications.append(
            f"<task_notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>completed</status>\n"
            f"  <command>{task['command']}</command>\n"
            f"  <summary>{summary}</summary>\n"
            f"</task_notification>"
        )
        print(
            f"  \033[32m[background done] {bg_id}: "
            f"{task['command'][:40]} ({len(output)} chars)\033[0m"
        )
    return notifications


def agent_loop(messages: list, context: dict):
    max_turns = 25
    system = get_system_prompt(context)
    messages[0] = {**messages[0], "content": system}
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
            args = json.loads(tc.function.arguments or "{}")
            if should_run_background(tc.function.name, args):
                bg_id = start_background_task(tc, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[Background task {bg_id} started] "
                        f"Command: {args.get('command', tc.function.name)}. "
                        f"Result will be available when complete.",
                    }
                )
                print(f"[background] {bg_id} started for tool '{tc.function.name}'")
                continue

            result = execute_tool(tc)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result[:200]}")

        user_content = []
        bg_notifications = collect_background_results()
        if bg_notifications:
            for notif in bg_notifications:
                user_content.append(notif)
            print(
                f"  \033[32m[inject] {len(bg_notifications)} background "
                f"notification(s)\033[0m"
            )
            messages.append({"role": "user", "content": "\n".join(user_content)})

        context = update_context(context, messages)
        system = get_system_prompt(context)
        messages[0] = {**messages[0], "content": system}
    print("达到最大轮次")


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    context = update_context({}, [])
    system = get_system_prompt(context)
    messages = [{"role": "system", "content": system}]
    while True:
        user_input = input(">>> ")
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages, context)
        context = update_context(context, messages)
