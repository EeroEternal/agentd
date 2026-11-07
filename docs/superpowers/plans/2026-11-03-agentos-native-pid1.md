# AgentOS Native PID 1 Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boot the laptop with `agentd` as PID 1, using native process supervision instead of systemd, with working network, SSH, tasks, and workflows.

**Architecture:** A native supervisor manages foreground child processes and logs. A separate PID 1 Python entry point mounts runtime filesystems, starts the supervisor and agentd socket, and launches prioritized persistent service definitions. A new Limine entry selects this init without modifying existing entries.

**Tech Stack:** Python standard library, Linux proc/sys/dev, subprocess/signals, NetworkManager, D-Bus, sshd, Limine.

---

## File map

- Create `agentd/native.py`: persistent native service definitions and process supervision.
- Create `agentd/native-init.py`: PID 1 setup, service startup, socket thread, and shutdown.
- Modify `agentd/systemd.py`: native-aware `system.status`.
- Modify `agentd/workflows.py`: permit native methods in workflows.
- Modify `agentd/server.py`: dispatch native methods.
- Remote create `/etc/agentd/services/`: native service definitions.
- Remote create `/usr/local/lib/agentd/native-init.py`: PID 1 entry point.
- Remote modify `/boot/limine.conf`: append `AgentOS Native` after all checks.

### Task 1: Implement the native process supervisor

**Files:**
- Create: `agentd/native.py`

- [ ] **Step 1: Implement validation and persistent definitions**

```python
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFINITIONS = Path("/etc/agentd/services")
LOGS = Path("/var/log/agentd")
NAME = re.compile(r"^agentos-[a-zA-Z0-9_.@-]+$")
RESTART_POLICIES = {"no", "always", "on-failure", "on-abnormal"}


def now():
    return datetime.now(timezone.utc).isoformat()


class NativeSupervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.services = {}
        DEFINITIONS.mkdir(parents=True, exist_ok=True, mode=0o700)
        LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _name(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("native service name must use the agentos- prefix")
        return name

    def _path(self, name):
        return DEFINITIONS / f"{self._name(name)}.json"

    def _definition(self, name):
        return json.loads(self._path(name).read_text(encoding="utf-8"))

    def _write_definition(self, definition):
        target = self._path(definition["name"])
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    def install(self, params):
        name = self._name(params.get("name"))
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty string array")
        if not argv[0].startswith("/") or not Path(argv[0]).is_file():
            raise ValueError("argv[0] must be an absolute executable path")
        restart = params.get("restart", "on-failure")
        if restart not in RESTART_POLICIES:
            raise ValueError("invalid restart policy")
        restart_sec = params.get("restart_sec", 1)
        if isinstance(restart_sec, bool) or not isinstance(restart_sec, int) or not 0 <= restart_sec <= 60:
            raise ValueError("restart_sec must be between 0 and 60")
        priority = params.get("priority", 100)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
            raise ValueError("priority must be between 0 and 1000")
        enabled = params.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        definition = {
            "name": name,
            "argv": argv,
            "restart": restart,
            "restart_sec": restart_sec,
            "priority": priority,
            "enabled": enabled,
        }
        self._write_definition(definition)
        with self.lock:
            if name not in self.services:
                self.services[name] = {
                    "definition": definition,
                    "process": None,
                    "monitor": None,
                    "status": "stopped",
                    "pid": 0,
                    "restarts": 0,
                    "exit_code": None,
                    "started_at": None,
                    "stop_requested": False,
                }
        return definition
```

- [ ] **Step 2: Implement start, monitor, stop, status, and logs**

Append:

```python
    def start(self, name):
        name = self._name(name)
        with self.lock:
            service = self.services.get(name)
            if service is None:
                self.install({"name": name, **self._definition(name)})
                service = self.services[name]
            process = service["process"]
            if process is not None and process.poll() is None:
                return self._status(service)
            definition = service["definition"]
            log_path = LOGS / f"{name}.log"
            log = log_path.open("ab")
            process = subprocess.Popen(
                definition["argv"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.close()
            service.update({
                "process": process,
                "status": "running",
                "pid": process.pid,
                "exit_code": None,
                "started_at": now(),
                "stop_requested": False,
            })
            monitor = threading.Thread(target=self._monitor, args=(name, process), daemon=True)
            service["monitor"] = monitor
            monitor.start()
            return self._status(service)

    def _monitor(self, name, process):
        exit_code = process.wait()
        with self.lock:
            service = self.services[name]
            if service["process"] is not process:
                return
            service["status"] = "stopped"
            service["exit_code"] = exit_code
            stop_requested = service["stop_requested"]
            definition = service["definition"]
        restart = definition["restart"]
        should_restart = not stop_requested and (
            restart == "always"
            or (restart == "on-failure" and exit_code != 0)
            or (restart == "on-abnormal" and exit_code < 0)
        )
        if should_restart:
            with self.lock:
                service["restarts"] += 1
                service["status"] = "restarting"
            time.sleep(definition["restart_sec"])
            self.start(name)

    def stop(self, name):
        name = self._name(name)
        with self.lock:
            service = self.services.get(name)
            if service is None:
                self.install({"name": name, **self._definition(name)})
                service = self.services[name]
            process = service["process"]
            service["stop_requested"] = True
            monitor = service["monitor"]
        if process is None or process.poll() is not None:
            service["status"] = "stopped"
            return self._status(service)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=5)
        with self.lock:
            service["status"] = "stopped"
        return self._status(service)

    def restart(self, name):
        self.stop(name)
        return self.start(name)

    def _status(self, service):
        return {
            "name": service["definition"]["name"],
            "status": service["status"],
            "pid": service["pid"],
            "restarts": service["restarts"],
            "exit_code": service["exit_code"],
            "started_at": service["started_at"],
        }

    def status(self, name):
        name = self._name(name)
        with self.lock:
            if name not in self.services:
                self.install({"name": name, **self._definition(name)})
            return self._status(self.services[name])

    def logs(self, name, lines=20):
        name = self._name(name)
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 200:
            raise ValueError("lines must be between 1 and 200")
        path = LOGS / f"{name}.log"
        if not path.exists():
            return {"name": name, "logs": ""}
        content = path.read_text(encoding="utf-8", errors="replace")
        return {"name": name, "logs": "\n".join(content.splitlines()[-lines:])}

    def list(self):
        names = {path.stem for path in DEFINITIONS.glob("*.json")}
        return [self.status(name) for name in sorted(names)]

    def start_enabled(self):
        definitions = [json.loads(path.read_text(encoding="utf-8")) for path in DEFINITIONS.glob("*.json")]
        definitions.sort(key=lambda definition: definition["priority"])
        for definition in definitions:
            if definition["enabled"]:
                self.start(definition["name"])

    def stop_all(self):
        for service in reversed(self.list()):
            try:
                self.stop(service["name"])
            except Exception:
                pass


SUPERVISOR = NativeSupervisor()
```

- [ ] **Step 3: Check supervisor syntax and invalid names**

Run:

```bash
python3 -m py_compile agentd/native.py
python3 -c 'from agentd.native import NativeSupervisor; s=NativeSupervisor(); s._name("agentos-test"); s._name("ssh")'
```

Expected: final command exits `1` with the prefix validation error.

### Task 2: Implement PID 1 initialization and native status

**Files:**
- Create: `agentd/native-init.py`
- Modify: `agentd/systemd.py`
- Modify: `agentd/workflows.py`
- Modify: `agentd/server.py`

- [ ] **Step 1: Create native-init.py**

```python
#!/usr/bin/python
import ctypes
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from agentd.native import SUPERVISOR
from agentd.server import serve

MOUNTS = (
    ("proc", "/proc", "proc", "nosuid,nodev,noexec"),
    ("sysfs", "/sys", "sysfs", "nosuid,nodev,noexec"),
    ("devtmpfs", "/dev", "devtmpfs", "nosuid"),
)


def mounted(target):
    try:
        mounts = Path("/proc/self/mounts").read_text().splitlines()
    except FileNotFoundError:
        return False
    return any(line.split()[1] == target for line in mounts)


def initialize():
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/bin"
    socket.sethostname("minipad")
    for source, target, filesystem, options in MOUNTS:
        Path(target).mkdir(parents=True, exist_ok=True)
        if not mounted(target):
            subprocess.run(["/usr/bin/mount", "-t", filesystem, "-o", options, source, target], check=True)
    if not mounted("/run"):
        subprocess.run(["/usr/bin/mount", "-t", "tmpfs", "-o", "nosuid,nodev,mode=755", "run", "/run"], check=True)
    for directory in (
        "/run/agentd", "/run/dbus", "/run/sshd", "/run/udev",
        "/run/systemd/resolve", "/run/agentos",
    ):
        Path(directory).mkdir(parents=True, exist_ok=True)
    Path("/run/agentos/native").touch()


def shutdown(signum, frame):
    print(f"agentos native: signal {signum}, stopping services", flush=True)
    SUPERVISOR.stop_all()
    os.sync()
    libc = ctypes.CDLL(None)
    libc.syscall(169, 0xfee1dead, 672274793, 0x4321fedc, 0)
    while True:
        time.sleep(3600)


def main():
    print("agentos native: initializing PID 1", flush=True)
    initialize()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    SUPERVISOR.start_enabled()
    print("agentos native: initialization complete", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add native-aware system status**

At the start of `system_status()` in `agentd/systemd.py`, add:

```python
    if Path("/run/agentos/native").exists():
        from .native import SUPERVISOR
        release = run(["/usr/bin/uname", "-r"], 5)["stdout"].strip()
        ssh = SUPERVISOR.status("agentos-sshd")
        return {
            "kernel_release": release,
            "hostname": socket.gethostname(),
            "init": "agentd-native",
            "systemd": "not-present",
            "agentd": "active",
            "sshd": ssh["status"],
            "time": datetime.now(timezone.utc).isoformat(),
        }
```

- [ ] **Step 3: Add native methods to workflows**

Add to `ALLOWED_METHODS` in `agentd/workflows.py`:

```python
    "native.install", "native.start", "native.stop", "native.restart",
    "native.status", "native.logs", "native.list",
```

- [ ] **Step 4: Add native dispatch methods**

In `dispatch` in `agentd/server.py`, add before the final unknown-method error:

```python
        if method == "native.install":
            return NATIVE.install(params)
        if method == "native.start":
            return NATIVE.start(params.get("name"))
        if method == "native.stop":
            return NATIVE.stop(params.get("name"))
        if method == "native.restart":
            return NATIVE.restart(params.get("name"))
        if method == "native.status":
            return NATIVE.status(params.get("name"))
        if method == "native.logs":
            return NATIVE.logs(params.get("name"), params.get("lines", 20))
        if method == "native.list":
            return NATIVE.list()
```

Reuse the same supervisor singleton used by PID 1:

```python
from .native import SUPERVISOR as NATIVE
```

Do not construct a second supervisor in the server.

- [ ] **Step 5: Check all source syntax**

Run:

```bash
python3 -m py_compile agentd/native.py agentd/native-init.py agentd/systemd.py agentd/workflows.py agentd/server.py
```

Expected: exit `0`.

### Task 3: Install native services and the boot entry

**Files:**
- Remote create: `/etc/agentd/services/*.json`
- Remote create: `/usr/local/lib/agentd/native-init.py`
- Remote modify: `/boot/limine.conf`

- [ ] **Step 1: Deploy source and make PID 1 executable**

Run:

```bash
scp -r agentd admin@192.168.3.11:/tmp/agentd-native
ssh admin@192.168.3.11 'sudo cp -a /tmp/agentd-native/. /usr/local/lib/agentd/agentd/ && sudo install -m 0755 /usr/local/lib/agentd/agentd/native-init.py /usr/local/lib/agentd/native-init.py && sudo /usr/bin/python -m py_compile /usr/local/lib/agentd/native-init.py'
```

Expected: compile succeeds.

- [ ] **Step 2: Install essential native definitions**

Using `native.install` through the running agentd, install:

```text
agentos-udev       argv ["/usr/lib/systemd/systemd-udevd"]              priority 10
agentos-dbus       argv ["/usr/bin/dbus-daemon", "--system", "--nofork"] priority 20
agentos-network    argv ["/usr/bin/NetworkManager", "--no-daemon"]      priority 30
agentos-resolved   argv ["/usr/lib/systemd/systemd-resolved"]           priority 40
agentos-sshd       argv ["/usr/bin/sshd", "-D"]                         priority 50
```

All use `restart="on-failure"`, `restart_sec=1`, and `enabled=true`. Do not start or stop these definitions in the current systemd boot; only persist them.

- [ ] **Step 3: Append the AgentOS Native entry**

Back up `/boot/limine.conf` to `/srv/agentos/artifacts/agent2/limine.conf.native.before`. Append exactly:

```text

/+AgentOS Native
  //linux
  comment: AgentOS native PID 1 prototype
  protocol: linux
  path: boot():/agentos/agent2/vmlinuz
  module_path: boot():/agentos/agent2/initramfs
  cmdline: cryptdevice=PARTUUID=e553df7c-02:root root=/dev/mapper/root zswap.enabled=0 rootflags=subvol=@ rw rootfstype=btrfs loglevel=7 init=/usr/local/lib/agentd/native-init.py
```

Assert the previous content is preserved, the new entry occurs once, and `default_entry: 2` remains. Do not reboot automatically.

### Task 4: Verify the native boot and autonomous task

**Files:**
- Remote create after native boot: `/srv/agentos/artifacts/agent2/native-boot-verification.txt`

- [ ] **Step 1: Reboot with operator present**

The operator selects `AgentOS Native` and unlocks LUKS. If initialization fails, record the console error and return to original Omarchy or agent2.

- [ ] **Step 2: Verify PID 1, mounts, services, and SSH**

Run:

```bash
ssh admin@192.168.3.11 'ps -p 1 -o pid,comm,args; cat /proc/agent_status; findmnt /run; ip -brief address; sudo env PYTHONPATH=/usr/local/lib/agentd python -m agentd call system.status "{}"; sudo env PYTHONPATH=/usr/local/lib/agentd python -m agentd call native.list "{}"'
```

Expected: PID 1 is native-init.py, Wi-Fi has `192.168.3.11`, native init is reported, and essential services are running. Save the output to the verification file.

- [ ] **Step 3: Verify native failure recovery**

Install `agentos-heartbeat-native` with argv `["/usr/bin/bash", "-c", "while true; do echo heartbeat; sleep 5; done"]`, restart on failure, and start it. Read PID and restart count, kill with SIGKILL, and poll `native.status` until active with a new PID and increased restart count. Verify native logs and task events.

- [ ] **Step 4: Verify a native workflow**

Submit a workflow that calls `system.status`, `native.restart`, and `native.status` with `expect={"status":"running"}`. Expected: workflow and task succeed; all step events are present.

- [ ] **Step 5: Verify existing recovery entries**

Do not reboot unless the operator requests it. Confirm the Limine file still contains Omarchy, AgentOS Test, AgentOS Test 2, and AgentOS Native, and that the default remains unchanged. The prototype is complete only after native SSH, service recovery, and workflow validation succeed.
