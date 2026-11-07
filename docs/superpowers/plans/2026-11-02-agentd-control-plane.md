# AgentD Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent tasks, structured system/service management, and a verified heartbeat failure-recovery workflow to the root Omarchy `agentd`.

**Architecture:** Keep the existing executor and Unix socket transport. Add a locked atomic JSON task store and a separate system-management module; the server dispatches methods and records operation events without storing full command output in task files.

**Tech Stack:** Python 3 standard library, systemd, journalctl, JSON-lines over Unix sockets.

---

## File map

- Create `agentd/store.py`: task lifecycle, event persistence, and atomic writes.
- Create `agentd/systemd.py`: system status and AgentOS service management.
- Modify `agentd/server.py`: method dispatch and task-event association.
- Modify `agentd/__main__.py`: generic `call METHOD JSON` CLI while preserving `exec`.
- Remote deploy root: `/usr/local/lib/agentd/agentd/`.
- Remote task root: `/var/lib/agentd/tasks/`.
- Remote demo unit: `/etc/systemd/system/agentos-heartbeat.service`.

### Task 1: Implement the persistent task store

**Files:**
- Create: `agentd/store.py`

- [ ] **Step 1: Implement task creation, reads, events, completion, and failure**

```python
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
```

- [ ] **Step 2: Exercise persistence and lifecycle in a temporary directory**

Run:

```bash
python3 -m py_compile agentd/store.py
python3 -c 'import tempfile; from agentd.store import TaskStore; root=tempfile.mkdtemp(); s=TaskStore(root); t=s.create("verify persistence"); s.record(t["id"],"check",True,{"run_id":"abc"}); assert TaskStore(root).get(t["id"])["status"] == "running"; done=s.complete(t["id"]); assert done["status"] == "succeeded"; assert len(done["events"]) == 3; print(done["id"], done["status"])'
```

Expected: syntax succeeds and the smoke command prints a task ID followed by `succeeded`.

### Task 2: Implement system status and service management

**Files:**
- Create: `agentd/systemd.py`

- [ ] **Step 1: Implement validated service operations**

```python
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

from .executor import execute

UNIT_DIR = Path("/etc/systemd/system")
UNIT_NAME = re.compile(r"^agentos-[a-zA-Z0-9_.@-]+\.service$")
RESTART_POLICIES = {"no", "always", "on-failure", "on-abnormal"}


def run(argv, timeout=60):
    result = execute(argv, timeout)
    if result["exit_code"] != 0:
        detail = result["stderr"] or result["stdout"]
        raise RuntimeError(f"{argv[0]} exited {result['exit_code']}: {detail}")
    return result


def unit_name(name):
    if not isinstance(name, str) or not UNIT_NAME.fullmatch(name):
        raise ValueError("service name must be an agentos-*.service unit")
    return name


def system_status():
    release = run(["/usr/bin/uname", "-r"], 5)["stdout"].strip()
    systemd = execute(["/usr/bin/systemctl", "is-system-running"], 5)
    agentd = execute(["/usr/bin/systemctl", "is-active", "agentd"], 5)
    sshd = execute(["/usr/bin/systemctl", "is-active", "sshd"], 5)
    for result in (systemd, agentd, sshd):
        if not result["stdout"].strip():
            raise RuntimeError("systemd status query returned no state")
    return {
        "kernel_release": release,
        "hostname": socket.gethostname(),
        "systemd": systemd["stdout"].strip(),
        "agentd": agentd["stdout"].strip(),
        "sshd": sshd["stdout"].strip(),
        "time": datetime.now(timezone.utc).isoformat(),
    }


def install(params):
    name = unit_name(params.get("name"))
    command = params.get("command")
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        raise ValueError("command must be a non-empty single-line string")
    restart = params.get("restart", "on-failure")
    if restart not in RESTART_POLICIES:
        raise ValueError("invalid restart policy")
    restart_sec = params.get("restart_sec", 1)
    if isinstance(restart_sec, bool) or not isinstance(restart_sec, int) or not 0 <= restart_sec <= 3600:
        raise ValueError("restart_sec must be between 0 and 3600")
    content = (
        "[Unit]\n"
        f"Description=AgentOS managed service: {name}\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        f"Restart={restart}\n"
        f"RestartSec={restart_sec}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    target = UNIT_DIR / name
    temporary = target.with_suffix(".agentd.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    reload_result = run(["/usr/bin/systemctl", "daemon-reload"])
    return {"name": name, "unit_path": str(target), "daemon_reload_run_id": reload_result["run_id"]}


def action(params, verb):
    name = unit_name(params.get("name"))
    result = run(["/usr/bin/systemctl", verb, name], 60)
    return {"name": name, "action": verb, "run_id": result["run_id"]}


def status(params):
    name = unit_name(params.get("name"))
    properties = [
        "LoadState", "ActiveState", "SubState", "MainPID",
        "NRestarts", "ExecMainStatus",
    ]
    result = run([
        "/usr/bin/systemctl", "show", name, "--no-pager",
        *(f"--property={name}" for name in properties),
    ], 10)
    values = {}
    for line in result["stdout"].splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    if values.get("LoadState") != "loaded":
        raise RuntimeError(f"service {name} is not loaded")
    return {"name": name, "properties": values, "run_id": result["run_id"]}


def logs(params):
    name = unit_name(params.get("name"))
    lines = params.get("lines", 20)
    if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 200:
        raise ValueError("lines must be between 1 and 200")
    result = run([
        "/usr/bin/journalctl", "-u", name, "-n", str(lines), "--no-pager",
    ], 10)
    return {"name": name, "logs": result["stdout"], "run_id": result["run_id"]}
```

- [ ] **Step 2: Check syntax and invalid-name rejection**

Run:

```bash
python3 -m py_compile agentd/systemd.py
python3 -c 'from agentd.systemd import unit_name; assert unit_name("agentos-heartbeat.service"); unit_name("ssh.service")'
```

Expected: final command exits `1` with `ValueError: service name must be an agentos-*.service unit`; no system command is executed.

### Task 3: Add server dispatch and generic CLI calls

**Files:**
- Modify: `agentd/server.py`
- Modify: `agentd/__main__.py`

- [ ] **Step 1: Replace server dispatch while retaining socket creation and umask fix**

Use the existing imports and constants, then add:

```python
from .store import TaskStore
from .systemd import action as service_action
from .systemd import install as service_install
from .systemd import logs as service_logs
from .systemd import status as service_status
from .systemd import system_status

STORE = TaskStore()
```

Replace `Handler` with:

```python
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request_id = None
        method = None
        task_id = None
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise ValueError("request is empty or too large")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            task_id = params.get("task_id")

            if method == "exec":
                result = execute(
                    params.get("argv"),
                    params.get("timeout", 60),
                    params.get("cwd"),
                )
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
            else:
                raise ValueError("unknown method")

            if task_id and method not in {"task.create", "task.get", "task.list", "task.complete", "task.fail"}:
                details = {}
                if isinstance(result, dict):
                    for key in ("run_id", "daemon_reload_run_id"):
                        if key in result:
                            details[key] = result[key]
                STORE.record(task_id, method, True, details)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:
            if task_id and method:
                try:
                    STORE.record(task_id, method, False, {"error": str(error)})
                except Exception:
                    pass
            response = {
                "id": request_id,
                "ok": False,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
```

- [ ] **Step 2: Generalize the CLI request function and add `call`**

In `agentd/__main__.py`, replace `request` with:

```python
def request(method, params, timeout=60):
    payload = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(max(timeout + 5, 10))
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode() + b"\n")
        response = client.makefile("rb").readline(2_097_153)
    if not response or len(response) > 2_097_152:
        raise RuntimeError("invalid response")
    return json.loads(response)
```

Replace `main` with:

```python
def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "server":
        serve()
        return 0
    if len(sys.argv) >= 5 and sys.argv[1] == "exec" and sys.argv[2] == "--timeout":
        response = request("exec", {"argv": sys.argv[4:], "timeout": int(sys.argv[3])}, int(sys.argv[3]))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") and response["result"]["exit_code"] == 0 else 1
    if len(sys.argv) == 4 and sys.argv[1] == "call":
        params = json.loads(sys.argv[3])
        if not isinstance(params, dict):
            raise ValueError("call parameters must be a JSON object")
        response = request(sys.argv[2], params)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") else 1
    print("usage: python -m agentd server | exec --timeout SECONDS ARGV... | call METHOD JSON", file=sys.stderr)
    return 2
```

- [ ] **Step 3: Check all local syntax**

Run:

```bash
python3 -m py_compile agentd/__init__.py agentd/executor.py agentd/store.py agentd/systemd.py agentd/server.py agentd/__main__.py
```

Expected: exit `0`.

### Task 4: Deploy and verify control-plane primitives

**Files:**
- Remote modified: `/usr/local/lib/agentd/agentd/`
- Remote created: `/var/lib/agentd/tasks/`

- [ ] **Step 1: Copy source and restart agentd**

Run:

```bash
scp -r agentd admin@192.168.3.11:/tmp/agentd-control-plane
ssh admin@192.168.3.11 'sudo cp -a /tmp/agentd-control-plane/. /usr/local/lib/agentd/agentd/ && sudo systemctl restart agentd && sudo systemctl is-active agentd'
```

Expected: `active`.

- [ ] **Step 2: Verify system status and existing exec compatibility**

Run:

```bash
ssh admin@192.168.3.11 'sudo env PYTHONPATH=/usr/local/lib/agentd python -m agentd call system.status "{}" && sudo env PYTHONPATH=/usr/local/lib/agentd python -m agentd exec --timeout 5 /usr/bin/id'
```

Expected: status reports `7.1.9-agent2` and active services; exec still reports `uid=0(root)`.

- [ ] **Step 3: Verify task persistence across agentd restart**

Run:

```bash
ssh admin@192.168.3.11 'sudo env PYTHONPATH=/usr/local/lib/agentd python -m agentd call task.create '"'"'{"goal":"verify task persistence"}'"'"''
```

Record the returned task ID, call `task.get`, restart `agentd`, and call `task.get` again with the same ID. Expected: identical goal and persistent event history.

### Task 5: Execute the heartbeat management and failure-recovery loop

**Files:**
- Remote create: `/etc/systemd/system/agentos-heartbeat.service`
- Remote create: persistent task JSON under `/var/lib/agentd/tasks/`

- [ ] **Step 1: Create the high-level task and inspect the system**

Use `task.create` with goal `创建并维护 agentos-heartbeat 服务，验证故障恢复`, then `system.status` with its task ID. Expected: task moves to `running` and system status is complete.

- [ ] **Step 2: Install and start heartbeat**

Call `service.install` with:

```json
{
  "name": "agentos-heartbeat.service",
  "command": "/usr/bin/bash -c 'while true; do echo heartbeat; sleep 5; done'",
  "restart": "on-failure",
  "restart_sec": 1,
  "task_id": "<task-id>"
}
```

Then call `service.start` with the same name and task ID. Expected: both operations succeed.

- [ ] **Step 3: Verify active state and logs**

Call `service.status` and `service.logs`. Expected: `ActiveState=active`, nonzero `MainPID`, and heartbeat log output.

- [ ] **Step 4: Simulate a crash and verify recovery**

Read the initial MainPID and NRestarts. Use the existing `exec` method with argv `["/usr/bin/kill", "-KILL", "<initial-main-pid>"]` and the task ID. Poll `service.status` once per second for up to 20 seconds. Expected within the timeout: active service, a different nonzero MainPID, and NRestarts greater than the initial value. On timeout, call `task.fail` with the observed state.

- [ ] **Step 5: Complete and audit the task**

Call `task.complete`, then `task.get`. Expected: status `succeeded`; ordered events include system status, install, start, status, logs, exec kill, and recovery status. Confirm `system.status` reports active agentd and sshd. Do not reboot the machine.
