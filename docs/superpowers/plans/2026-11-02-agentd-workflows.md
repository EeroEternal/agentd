# AgentD Autonomous Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent background workflows with nested result assertions, retries, and explicit restart interruption handling to `agentd`.

**Architecture:** A workflow manager uses the existing `TaskStore` and a shared dispatcher. The server and workflow engine call the same method dispatcher, avoiding separate semantics. Workflow state is atomically persisted and active workflows are failed rather than replayed after an agentd restart.

**Tech Stack:** Python standard library, threading, JSON, systemd, existing agentd methods.

---

## File map

- Create `agentd/workflows.py`: validation, persistence, execution, assertions, and restart recovery.
- Modify `agentd/server.py`: shared dispatcher plus workflow methods.
- Modify no kernel or boot files.

### Task 1: Implement the workflow engine

**Files:**
- Create: `agentd/workflows.py`

- [ ] **Step 1: Implement validation and nested assertions**

Add:

```python
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
    "exec", "system.status", "service.install", "service.start",
    "service.stop", "service.restart", "service.status", "service.logs",
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
```

- [ ] **Step 2: Implement the manager and durable workflow state**

Append:

```python
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
```

- [ ] **Step 3: Implement background execution**

Append:

```python
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
```

- [ ] **Step 4: Verify syntax and assertion behavior**

Run:

```bash
python3 -m py_compile agentd/workflows.py
python3 -c 'from agentd.workflows import assert_expected, validate_steps; assert_expected({"properties":{"ActiveState":"active"}}, {"properties":{"ActiveState":"active"}}); validate_steps([{"name":"check","method":"system.status"}]); print("workflow primitives ok")'
```

Expected: exit `0` and print `workflow primitives ok`.

### Task 2: Expose the shared dispatcher and workflow API

**Files:**
- Modify: `agentd/server.py`

- [ ] **Step 1: Add imports and extract dispatch**

Add:

```python
from .workflows import WorkflowManager
```

Extract all method handling from `Handler.handle` into this function, leaving parameter parsing and error responses in `Handler`:

```python
TASK_METHODS = {"task.create", "task.get", "task.list", "task.complete", "task.fail"}


def dispatch(method, params):
    task_id = params.get("task_id")
    if task_id and method not in TASK_METHODS:
        task = STORE.get(task_id)
        if task["status"] in {"succeeded", "failed"}:
            raise ValueError("cannot execute operations for a completed task")
    try:
        if method == "exec":
            result = execute(params.get("argv"), params.get("timeout", 60), params.get("cwd"))
        elif method == "system.status":
            result = system_status()
        elif method == "service.install":
            result = service_install(params)
        elif method in {"service.start", "service.stop", "service.restart"}:
            result = service_action(params, method.split(".", 1)[1])
        elif method == "service.status":
            result = service_status(params)
        elif method == "service.logs":
            result = service_logs(params)
        elif method == "task.create":
            result = STORE.create(params.get("goal"))
        elif method == "task.get":
            result = STORE.get(params.get("task_id"))
        elif method == "task.list":
            result = STORE.list()
        elif method == "task.complete":
            result = STORE.complete(params.get("task_id"))
        elif method == "task.fail":
            result = STORE.fail(params.get("task_id"), params.get("reason"))
        elif method == "workflow.start":
            result = WORKFLOWS.start(params.get("goal"), params.get("steps"))
        elif method == "workflow.get":
            result = WORKFLOWS.get(params.get("workflow_id"))
        elif method == "workflow.list":
            result = WORKFLOWS.list()
        else:
            raise ValueError("unknown method")
    except Exception as error:
        if task_id and method not in TASK_METHODS:
            try:
                STORE.record(task_id, method, False, {"error": str(error)})
            except Exception:
                pass
        raise

    if task_id and method not in TASK_METHODS:
        details = {}
        if isinstance(result, dict):
            for key in ("run_id", "daemon_reload_run_id"):
                if key in result:
                    details[key] = result[key]
        STORE.record(task_id, method, True, details)
    return result


WORKFLOWS = WorkflowManager(STORE, dispatch)
```

In `Handler.handle`, replace the method chain with `result = dispatch(method, params)`. Remove task-event recording from `Handler`; the shared dispatcher records both external calls and workflow steps, and validates task state before side effects.

- [ ] **Step 2: Check complete source syntax**

Run:

```bash
python3 -m py_compile agentd/store.py agentd/systemd.py agentd/workflows.py agentd/server.py agentd/__main__.py
```

Expected: exit `0`.

### Task 3: Deploy and verify successful and failed workflows

**Files:**
- Remote modified: `/usr/local/lib/agentd/agentd/`
- Remote created: `/var/lib/agentd/workflows/`

- [ ] **Step 1: Deploy and restart**

Run:

```bash
scp -r agentd admin@192.168.3.11:/tmp/agentd-workflows
ssh admin@192.168.3.11 'sudo cp -a /tmp/agentd-workflows/. /usr/local/lib/agentd/agentd/ && sudo systemctl restart agentd && sudo systemctl is-active agentd'
```

Expected: `active`.

- [ ] **Step 2: Submit a successful heartbeat workflow**

Call `workflow.start` with goal `通过自主工作流重启并验证 heartbeat` and these steps:

```json
[
  {
    "name": "inspect",
    "method": "system.status",
    "params": {}
  },
  {
    "name": "install",
    "method": "service.install",
    "params": {
      "name": "agentos-heartbeat.service",
      "command": "/usr/bin/bash -c 'while true; do echo heartbeat; sleep 5; done'",
      "restart": "on-failure",
      "restart_sec": 1
    }
  },
  {
    "name": "restart",
    "method": "service.restart",
    "params": {"name": "agentos-heartbeat.service"}
  },
  {
    "name": "verify",
    "method": "service.status",
    "params": {"name": "agentos-heartbeat.service"},
    "expect": {"properties": {"ActiveState": "active", "SubState": "running"}},
    "attempts": 5,
    "delay": 1
  },
  {
    "name": "logs",
    "method": "service.logs",
    "params": {"name": "agentos-heartbeat.service", "lines": 5}
  }
]
```

Poll `workflow.get` until terminal. Expected: `succeeded`; all steps succeeded; associated task is `succeeded`; heartbeat remains active.

- [ ] **Step 3: Submit a failing workflow**

Call `workflow.start` with goal `验证失败工作流记录` and one step:

```json
{
  "name": "missing-service",
  "method": "service.status",
  "params": {"name": "agentos-missing.service"}
}
```

Expected: workflow and task become `failed`; error identifies that the service is not loaded; heartbeat remains active.

- [ ] **Step 4: Verify restart interruption handling**

Start a workflow with one `exec` step running `["/usr/bin/sleep", "30"]`, then restart agentd while it is running. Expected after restart: persisted workflow is `failed` with `agentd restarted while workflow was active`; associated task is `failed`; no sleep process remains.

- [ ] **Step 5: Final health check**

Run:

```bash
ssh admin@192.168.3.11 'uname -r; systemctl is-system-running; systemctl is-active agentd sshd agentos-heartbeat.service'
```

Expected: `7.1.9-agent2`, `running`, and three `active` results.
