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
    subject: str = Field(..., description="Subject of the task")
    description: str = Field(..., description="Description of the task")
    status: TaskStatus = Field(
        TaskStatus.PENDING, description="Current status of the task"
    )
    owner: str | None = Field(None, description="Owner of the task")
    blockedBy: list[str] = Field(
        default_factory=list, description="List of task IDs that block this task"
    )


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def create_task(
    subject: str, description: str, blockedBy: list[str] | None = None
) -> Task:
    task_id = f"task_{int(time.time())}_{random.randint(0, 9999):04d}"
    task = Task(
        id=task_id,
        subject=subject,
        status=TaskStatus.PENDING,
        owner=None,
        description=description,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def save_task(task: Task):
    _task_path(task.id).write_text(task.model_dump_json(indent=2))


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def list_tasks() -> list[Task]:
    return [
        Task(**json.loads(f.read_text())) for f in sorted(TASKS_DIR.glob("task_*.json"))
    ]


def get_task(task_id: str) -> str:
    return Task(**json.loads(_task_path(task_id).read_text())).model_dump_json(indent=2)


def can_start(task_id: str) -> bool:
    task = load_task(task_id)
    for t in task.blockedBy:
        if not _task_path(t).exists():
            return False
        else:
            blocked_task = load_task(t)
            if blocked_task.status != TaskStatus.COMPLETED:
                return False
    return True


def claim_task(task_id: str, owner="agent") -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.PENDING:
        return f"Error: Task {task_id} is not pending and cannot be claimed."
    if not can_start(task_id):
        deps = [
            d
            for d in task.blockedBy
            if not _task_path(d).exists() or load_task(d).status != TaskStatus.COMPLETED
        ]
        return f"Error: Task {task_id} cannot be claimed blocked by incomplete tasks: {', '.join(deps)}"
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
    Unblocked = []
    for t in list_tasks():
        if t.status == TaskStatus.PENDING and t.blockedBy and can_start(t.id):
            Unblocked.append(t.id)
    return f"Task {task_id} marked as completed. Unblocked tasks: {', '.join(Unblocked) if Unblocked else 'None'}"


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


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


class CreateTaskArgs(BaseModel):
    subject: str = Field(..., description="the subject of the task")
    description: str = Field(..., description="the description of the task")
    blockedBy: list[str] | None = Field(
        default_factory=list, description="list of task IDs that block this task"
    )


class TaskIdArgs(BaseModel):
    task_id: str = Field(..., description="the unique identifier of the task")


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
            cwd=os.getcwd(),
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
    subject: str, description: str, blockedBy: list[str] | None = None
) -> str:
    task = create_task(subject, description, blockedBy)
    return f"Task created with ID: {task.id}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks found."
    return "\n".join(
        [f"{task.id}: {task.subject} (Status: {task.status})" for task in tasks]
    )


def run_get_task(task_id: str) -> str:
    try:
        task = load_task(task_id)
        return task.model_dump_json(indent=2)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found."


def run_claim_task(task_id: str, owner: str = "agent") -> str:
    result = claim_task(task_id, owner)
    return result


def run_complete_task(task_id: str) -> str:
    result = complete_task(task_id)
    return result


TOOL_REGISTRY = {
    "bash": ("Run a shell command.", BashArgs, run_bash),
    "create_task": (
        "Create a new task with subject, description, and optional blockedBy list.",
        CreateTaskArgs,
        run_create_task,
    ),
    "list_tasks": ("List all tasks.", NoneArgs, run_list_tasks),
    "get_task": ("Get details of a specific task by ID.", TaskIdArgs, run_get_task),
    "claim_task": ("Claim a task by ID.", TaskIdArgs, run_claim_task),
    "complete_task": ("Mark a task as completed by ID.", TaskIdArgs, run_complete_task),
}

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": f"Available tools: {', '.join(TOOL_REGISTRY.keys())}.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
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
    for name, (desc, args, func) in TOOL_REGISTRY.items()
]


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


def agent_loop(messages: list, context: dict):
    max_turns = 25
    system = get_system_prompt(context)
    messages[0] = {**messages[0], "content": system}
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
            entry = TOOL_REGISTRY.get(tc.function.name)
            if not entry:
                result = f"Error: unknown tool {tc.function.name}"
            else:
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
