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
TASK_DIR = WORKDIR / ".tasks"
TASK_DIR.mkdir(exist_ok=True)
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
    return TASK_DIR / f"{task_id}.json"

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

def list_tasks() -> list[Task]:
    return [Task(**json.loads(p.read_text())) for p in sorted(TASK_DIR.glob("task_*.json"))]

def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))

def get_task(task_id: str) -> str:
    task = load_task(task_id)
    return task.model_dump_json(indent=2)

def can_start(task_id: str) -> bool:
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            raise ValueError(f"Dependency task {dep_id} does not exist.")
        if load_task(dep_id).status != TaskStatus.COMPLETED:
            return False
    return True
