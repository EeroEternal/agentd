# AgentOS Health and Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional dependencies, readiness probes, startup timeouts, and aggregated health reporting to native AgentOS.

**Architecture:** Wrap the existing process start method with dependency recursion and readiness checks. A separate health module aggregates required services and machine state without changing boot defaults.

**Tech Stack:** Python standard library, native supervisor, pacman-independent system commands.

---

### Task 1: Add dependency and readiness support

**Files:**
- Modify: `agentd/native.py`

- [ ] **Step 1: Validate optional definition fields**

Add to `install` before constructing `definition`:

```python
        depends_on = params.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            raise ValueError("depends_on must be a string array")
        for dependency in depends_on:
            self._name(dependency)
        start_timeout = params.get("start_timeout", 30)
        if isinstance(start_timeout, bool) or not isinstance(start_timeout, int) or not 1 <= start_timeout <= 300:
            raise ValueError("start_timeout must be between 1 and 300 seconds")
        ready = params.get("ready")
        if ready is not None:
            if not isinstance(ready, dict) or not isinstance(ready.get("argv"), list):
                raise ValueError("ready must contain an argv array")
            interval = ready.get("interval", 1)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.1 <= interval <= 10:
                raise ValueError("ready interval must be between 0.1 and 10 seconds")
```

Include in `definition`:

```python
            "depends_on": depends_on,
            "start_timeout": start_timeout,
            "ready": ready,
```

- [ ] **Step 2: Add recursive startup and readiness checks**

Rename the existing `start` method to `_start_process`, then add:

```python
    def start(self, name):
        return self._start_with_dependencies(name, set())

    def _start_with_dependencies(self, name, seen):
        name = self._name(name)
        if name in seen:
            raise ValueError(f"dependency cycle detected at {name}")
        seen.add(name)
        if name not in self.services:
            self.install({"name": name, **self._definition(name)})
        for dependency in self.services[name]["definition"]["depends_on"]:
            self._start_with_dependencies(dependency, seen)
        result = self._start_process(name)
        try:
            return self._wait_ready(name)
        except Exception:
            self.stop(name)
            raise

    def _wait_ready(self, name):
        service = self.services[name]
        definition = service["definition"]
        process = service["process"]
        ready = definition.get("ready")
        if ready is None:
            return self._status(service)
        deadline = time.monotonic() + definition["start_timeout"]
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited before becoming ready")
            result = subprocess.run(ready["argv"], capture_output=True, timeout=10)
            if result.returncode == 0:
                return self._status(service)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name} did not become ready")
            time.sleep(ready.get("interval", 1))
```

Existing `_start_process` behavior, restart handling, and monitors remain unchanged. `start_enabled` now receives status only after each optional readiness check succeeds.

- [ ] **Step 3: Check syntax**

Run `python3 -m py_compile agentd/native.py`. Expected: exit `0`.

### Task 2: Add system.health

**Files:**
- Create: `agentd/health.py`
- Modify: `agentd/server.py`
- Modify: `agentd/workflows.py`

- [ ] **Step 1: Implement health aggregation**

```python
from datetime import datetime, timezone

from .native import SUPERVISOR
from .network import status as network_status
from .systemd import run

REQUIRED = {
    "agentos-udev", "agentos-dbus", "agentos-network",
    "agentos-resolved", "agentos-sshd",
}


def health():
    reasons = []
    services = {service["name"]: service for service in SUPERVISOR.list()}
    for name in sorted(REQUIRED):
        service = services.get(name)
        if service is None or service["status"] != "running":
            reasons.append(f"required service not running: {name}")
    storage = run(["/usr/bin/df", "-B1", "/"], 10)["stdout"]
    memory = {}
    for line in open("/proc/meminfo", encoding="utf-8"):
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}:
            memory[key] = int(value.strip().split()[0])
    network = network_status()
    return {
        "ready": not reasons,
        "reasons": reasons,
        "services": services,
        "storage": storage,
        "memory": memory,
        "network": network,
        "time": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 2: Add dispatch and workflow access**

In `dispatch`, add:

```python
        if method == "system.health":
            return system_health()
```

Import:

```python
from .health import health as system_health
```

Add `system.health` to `ALLOWED_METHODS` in `agentd/workflows.py`.

- [ ] **Step 3: Check syntax**

Run `python3 -m py_compile agentd/health.py agentd/server.py agentd/workflows.py`. Expected: exit `0`.

### Task 3: Deploy and validate

**Files:**
- Remote modify: `/usr/local/lib/agentd/agentd/`
- Remote create: `/etc/agentd/services/agentos-probe.json`

- [ ] **Step 1: Deploy updated files and reboot once**

Use the existing automatic unlock and SIGUSR1 clean reboot. Expected: native network and SSH recover without manual input.

- [ ] **Step 2: Verify dependency startup and readiness**

Install `agentos-probe` with `depends_on=["agentos-dbus"]`, `start_timeout=5`, and `ready={"argv":["/usr/bin/true"],"interval":0.2}`. Start it and expect `running`. Then install a second probe with `ready={"argv":["/usr/bin/false"],"interval":0.2}` and expect a timeout error, not a running result.

- [ ] **Step 3: Verify dependency fault recovery and health workflow**

Kill the D-Bus process, wait until its monitor restarts it, and call `system.health`. Expected: `ready:true`. Run a workflow with `system.health` and `expect={"ready":true}`; workflow and task must succeed.

- [ ] **Step 4: Verify existing behavior**

Confirm default entry remains `AgentOS Native/linux`, heartbeat is running, file/package/network methods work, and no local console is required.
