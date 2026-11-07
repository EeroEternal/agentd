import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

TASKS = Path("/var/lib/agentd/tasks")
TASK_ID = re.compile(r"^[0-9a-f]{32}$")
TERMINAL = {"succeeded", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, root=TASKS):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()

    def _path(self, task_id):
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise ValueError("invalid task id")
        return self.root / f"{task_id}.json"

    def _read(self, task_id):
        path = self._path(task_id)
        task = json.loads(path.read_text(encoding="utf-8"))
        if task.get("id") != task_id:
            raise ValueError("task id mismatch")
        return task

    def _write(self, task):
        path = self._path(task["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def create(self, goal):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4096:
            raise ValueError("goal must be a non-empty string of at most 4096 characters")
        task_id = uuid.uuid4().hex
        timestamp = now()
        task = {
            "id": task_id,
            "goal": goal,
            "status": "created",
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": None,
            "events": [],
        }
        with self.lock:
            task["events"].append({
                "time": timestamp,
                "action": "task.create",
                "ok": True,
                "details": {"goal": goal},
            })
            self._write(task)
        return task

    def get(self, task_id):
        with self.lock:
            return self._read(task_id)

    def list(self):
        with self.lock:
            tasks = [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*.json")]
        return sorted(tasks, key=lambda task: task["created_at"])

    def record(self, task_id, action, ok, details=None):
        if not isinstance(action, str) or not action:
            raise ValueError("action must be a non-empty string")
        with self.lock:
            task = self._read(task_id)
            if task["status"] in TERMINAL:
                raise ValueError("cannot append events to a completed task")
            timestamp = now()
            task["events"].append({
                "time": timestamp,
                "action": action,
                "ok": bool(ok),
                "details": details or {},
            })
            if task["status"] == "created":
                task["status"] = "running"
            task["updated_at"] = timestamp
            self._write(task)
        return task

    def complete(self, task_id):
        with self.lock:
            task = self._read(task_id)
            if task["status"] in TERMINAL:
                raise ValueError("task is already completed")
            timestamp = now()
            task["status"] = "succeeded"
            task["updated_at"] = timestamp
            task["completed_at"] = timestamp
            task["events"].append({
                "time": timestamp,
                "action": "task.complete",
                "ok": True,
                "details": {},
            })
            self._write(task)
        return task

    def fail(self, task_id, reason):
        if not isinstance(reason, str) or not reason:
            raise ValueError("failure reason must be a non-empty string")
        with self.lock:
            task = self._read(task_id)
            if task["status"] in TERMINAL:
                raise ValueError("task is already completed")
            timestamp = now()
            task["status"] = "failed"
            task["updated_at"] = timestamp
            task["completed_at"] = timestamp
            task["events"].append({
                "time": timestamp,
                "action": "task.fail",
                "ok": False,
                "details": {"reason": reason},
            })
            self._write(task)
        return task
