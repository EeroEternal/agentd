import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKFLOWS = Path("/var/lib/agentd/workflows")
ALLOWED_METHODS = {
    "exec", "cell.exec", "cell.status", "vm.run", "vm.status", "system.status", "service.install", "service.start",
    "service.stop", "service.restart", "service.status", "service.logs",
    "native.install", "native.start", "native.stop", "native.restart",
    "native.status", "native.logs", "native.list",
    "file.read", "file.write", "file.list", "file.delete", "file.restore",
    "package.info", "package.list", "package.install", "package.remove",
    "network.status", "system.health",
}
TERMINAL = {"succeeded", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_steps(steps):
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty array")
    names = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        name = step.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("step names must be non-empty and unique")
        names.add(name)
        if step.get("method") not in ALLOWED_METHODS:
            raise ValueError(f"method not allowed in workflow: {step.get('method')}")
        if not isinstance(step.get("params", {}), dict):
            raise ValueError("step params must be an object")
        attempts = step.get("attempts", 1)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 20:
            raise ValueError("attempts must be between 1 and 20")
        delay = step.get("delay", 0)
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 10:
            raise ValueError("delay must be between 0 and 10 seconds")
        if not isinstance(step.get("expect", {}), dict):
            raise ValueError("expect must be an object")
    return steps


def assert_expected(actual, expected):
    if not isinstance(expected, dict):
        raise ValueError("expect must be an object")
    for key, value in expected.items():
        if not isinstance(actual, dict) or key not in actual:
            raise AssertionError(f"missing expected field: {key}")
        if isinstance(value, dict):
            assert_expected(actual[key], value)
        elif actual[key] != value:
            raise AssertionError(f"unexpected {key}: got {actual[key]!r}, want {value!r}")


class WorkflowManager:
    def __init__(self, tasks, dispatcher, root=WORKFLOWS):
        self.tasks = tasks
        self.dispatcher = dispatcher
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()
        self.recover_interrupted()

    def _path(self, workflow_id):
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
            raise ValueError("invalid workflow id")
        return self.root / f"{workflow_id}.json"

    def _read(self, workflow_id):
        workflow = json.loads(self._path(workflow_id).read_text(encoding="utf-8"))
        if workflow.get("id") != workflow_id:
            raise ValueError("workflow id mismatch")
        return workflow

    def _write(self, workflow):
        path = self._path(workflow["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _update(self, workflow_id, **changes):
        with self.lock:
            workflow = self._read(workflow_id)
            workflow.update(changes)
            workflow["updated_at"] = now()
            self._write(workflow)
        return workflow

    def get(self, workflow_id):
        with self.lock:
            return self._read(workflow_id)

    def list(self):
        with self.lock:
            workflows = [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*.json")]
        return sorted(workflows, key=lambda workflow: workflow["created_at"])

    def recover_interrupted(self):
        for path in self.root.glob("*.json"):
            workflow = json.loads(path.read_text(encoding="utf-8"))
            if workflow["status"] in {"queued", "running"}:
                workflow["status"] = "failed"
                workflow["error"] = "agentd restarted while workflow was active"
                workflow["completed_at"] = now()
                workflow["updated_at"] = now()
                self._write(workflow)
                try:
                    self.tasks.fail(workflow["task_id"], workflow["error"])
                except ValueError:
                    pass
            elif workflow["status"] in TERMINAL:
                task = self.tasks.get(workflow["task_id"])
                if task["status"] not in TERMINAL:
                    if workflow["status"] == "succeeded":
                        self.tasks.complete(task["id"])
                    else:
                        self.tasks.fail(task["id"], workflow.get("error", "workflow failed"))

    def start(self, goal, steps):
        validate_steps(steps)
        task = self.tasks.create(goal)
        workflow_id = uuid.uuid4().hex
        timestamp = now()
        workflow = {
            "id": workflow_id,
            "task_id": task["id"],
            "goal": goal,
            "definition": steps,
            "status": "queued",
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "steps": [
                {
                    "name": step["name"],
                    "method": step["method"],
                    "status": "pending",
                    "attempts": 0,
                    "result": None,
                    "error": None,
                }
                for step in steps
            ],
        }
        self._write(workflow)
        thread = threading.Thread(target=self._run, args=(workflow_id, steps), daemon=True)
        thread.start()
        return workflow

    def _summary(self, result):
        if not isinstance(result, dict):
            return {}
        keys = {
            "run_id", "daemon_reload_run_id", "properties", "name", "action",
            "kernel_release", "systemd", "agentd", "sshd",
        }
        return {key: result[key] for key in keys if key in result}

    def _run(self, workflow_id, steps):
        self._update(workflow_id, status="running", started_at=now())
        for index, step in enumerate(steps):
            attempts = step.get("attempts", 1)
            delay = step.get("delay", 0)
            for attempt in range(1, attempts + 1):
                try:
                    self._update_step(workflow_id, index, status="running", attempts=attempt, error=None)
                    params = dict(step.get("params", {}))
                    params["task_id"] = self._read(workflow_id)["task_id"]
                    result = self.dispatcher(step["method"], params)
                    assert_expected(result, step.get("expect", {}))
                    self._update_step(
                        workflow_id, index, status="succeeded", attempts=attempt,
                        result=self._summary(result), error=None,
                    )
                    break
                except Exception as error:
                    final = attempt == attempts
                    self._update_step(
                        workflow_id, index, status="failed" if final else "retrying",
                        attempts=attempt, error=str(error),
                    )
                    if final:
                        self._update(workflow_id, status="failed", error=str(error), completed_at=now())
                        self.tasks.fail(self._read(workflow_id)["task_id"], f"{step['name']}: {error}")
                        return
                    time.sleep(delay)
        self._update(workflow_id, status="succeeded", completed_at=now())
        self.tasks.complete(self._read(workflow_id)["task_id"])

    def _update_step(self, workflow_id, index, **changes):
        with self.lock:
            workflow = self._read(workflow_id)
            workflow["steps"][index].update(changes)
            workflow["updated_at"] = now()
            self._write(workflow)
