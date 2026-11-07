# AgentOS Agent Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add atomic file operations, pacman management, and structured network status to native AgentOS and expose them in workflows.

**Architecture:** Separate file, package, and network modules. Package and network commands use the existing logged executor; file writes use temporary files, atomic replacement, and persistent backups.

**Tech Stack:** Python standard library, pacman, ip, nmcli, existing agentd dispatcher.

---

## File map

- Create `agentd/files.py`: file read/write/list/delete and backups.
- Create `agentd/packages.py`: pacman info/list/install/remove.
- Create `agentd/network.py`: address, route, resolver, and NetworkManager state.
- Modify `agentd/server.py`: resource dispatch.
- Modify `agentd/workflows.py`: permit resource methods.

### Task 1: Implement file resources

**Files:**
- Create: `agentd/files.py`

- [ ] **Step 1: Implement safe file operations**

```python
import hashlib
import os
import stat
import uuid
from pathlib import Path

MAX_BYTES = 1_048_576
BACKUPS = Path("/var/lib/agentd/backups")


def absolute(value):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value:
        raise ValueError("path must be an absolute string")
    return Path(value)


def read(params):
    path = absolute(params.get("path"))
    if not path.is_file():
        raise FileNotFoundError(path)
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("file exceeds the 1 MiB limit")
    return {
        "path": str(path),
        "content": data.decode("utf-8"),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def backup(path):
    if not path.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = BACKUPS / f"{uuid.uuid4().hex}-{path.name}"
    target.write_bytes(path.read_bytes())
    return str(target)


def write(params):
    path = absolute(params.get("path"))
    content = params.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("content must be text of at most 1 MiB")
    data = content.encode("utf-8")
    if params.get("parents", True):
        path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup(path) if params.get("backup", True) else None
    mode = params.get("mode")
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
        raise ValueError("invalid file mode")
    temporary = path.with_name(f".{path.name}.agentd.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return {
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "backup": backup_path,
    }


def list_directory(params):
    path = absolute(params.get("path"))
    if not path.is_dir():
        raise NotADirectoryError(path)
    entries = []
    for entry in path.iterdir():
        info = entry.lstat()
        entries.append({
            "name": entry.name,
            "type": "directory" if entry.is_dir() else "file" if entry.is_file() else "other",
            "size": info.st_size,
            "mode": stat.S_IMODE(info.st_mode),
        })
        if len(entries) >= 1000:
            break
    return {"path": str(path), "entries": entries}


def delete(params):
    path = absolute(params.get("path"))
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(path)
    if path.is_dir() and not path.is_symlink():
        raise IsADirectoryError(path)
    backup_path = backup(path) if params.get("backup", True) else None
    path.unlink()
    return {"path": str(path), "deleted": True, "backup": backup_path}
```

- [ ] **Step 2: Verify with a temporary directory**

Run:

```bash
python3 -m py_compile agentd/files.py
python3 -c 'import tempfile; from pathlib import Path; import agentd.files as files; root=Path(tempfile.mkdtemp())/"node.json"; files.BACKUPS=root.parent/"backups"; files.write({"path":str(root),"content":"{}\n"}); assert files.read({"path":str(root)})["content"] == "{}\n"; assert files.list_directory({"path":str(root.parent)})["entries"][0]["name"] == "node.json"; assert files.delete({"path":str(root)})["deleted"]; print("file resources ok")'
```

Expected: `file resources ok`.

### Task 2: Implement package resources

**Files:**
- Create: `agentd/packages.py`

- [ ] **Step 1: Implement validated pacman operations**

```python
import re

from .systemd import run

PACKAGE = re.compile(r"^[a-zA-Z0-9@._+:-]+$")


def names(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError("names must contain 1 to 20 packages")
    if not all(isinstance(name, str) and PACKAGE.fullmatch(name) for name in value):
        raise ValueError("invalid package name")
    return value


def info(params):
    name = names([params.get("name")])[0]
    result = run(["/usr/bin/pacman", "-Qi", name], 30)
    return {"name": name, "installed": True, "info": result["stdout"], "run_id": result["run_id"]}


def list_installed():
    result = run(["/usr/bin/pacman", "-Q"], 30)
    return {"packages": result["stdout"], "run_id": result["run_id"]}


def install(params):
    requested = names(params.get("names"))
    result = run(["/usr/bin/pacman", "-S", "--needed", "--noconfirm", *requested], 3600)
    return {"names": requested, "run_id": result["run_id"]}


def remove(params):
    requested = names(params.get("names"))
    result = run(["/usr/bin/pacman", "-Rns", "--noconfirm", *requested], 3600)
    return {"names": requested, "run_id": result["run_id"]}
```

- [ ] **Step 2: Check syntax and invalid names**

Run:

```bash
python3 -m py_compile agentd/packages.py
python3 -c 'from agentd.packages import names; assert names(["bash"]); names(["bad package"])'
```

Expected: invalid package name error; no pacman command executes.

### Task 3: Implement network status and dispatch

**Files:**
- Create: `agentd/network.py`
- Modify: `agentd/server.py`
- Modify: `agentd/workflows.py`

- [ ] **Step 1: Implement network status**

```python
from .native import SUPERVISOR
from .systemd import run


def status():
    addresses = run(["/usr/bin/ip", "-brief", "address"], 10)
    routes = run(["/usr/bin/ip", "route", "show", "default"], 10)
    devices = run([
        "/usr/bin/nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
        "device", "status",
    ], 15)
    resolver = run(["/usr/bin/readlink", "-f", "/etc/resolv.conf"], 5)
    return {
        "addresses": addresses["stdout"],
        "default_routes": routes["stdout"],
        "resolv_conf": resolver["stdout"].strip(),
        "devices": devices["stdout"],
        "ssh": SUPERVISOR.status("agentos-sshd"),
        "run_ids": [addresses["run_id"], routes["run_id"], devices["run_id"], resolver["run_id"]],
    }
```

- [ ] **Step 2: Add dispatch methods**

In `dispatch`, add:

```python
        if method == "file.read":
            return file_read(params)
        if method == "file.write":
            return file_write(params)
        if method == "file.list":
            return list_directory(params)
        if method == "file.delete":
            return file_delete(params)
        if method == "package.info":
            return package_info(params)
        if method == "package.list":
            return list_installed()
        if method == "package.install":
            return package_install(params)
        if method == "package.remove":
            return package_remove(params)
        if method == "network.status":
            return network_status()
```

Import aliases:

```python
from .files import delete as file_delete
from .files import list_directory
from .files import read as file_read
from .files import write as file_write
from .network import status as network_status
from .packages import info as package_info
from .packages import install as package_install
from .packages import list_installed
from .packages import remove as package_remove
```

- [ ] **Step 3: Permit workflow methods**

Add to `ALLOWED_METHODS`:

```python
    "file.read", "file.write", "file.list", "file.delete",
    "package.info", "package.list", "package.install", "package.remove",
    "network.status",
```

- [ ] **Step 4: Check syntax**

Run:

```bash
python3 -m py_compile agentd/files.py agentd/packages.py agentd/network.py agentd/server.py agentd/workflows.py
```

Expected: exit `0`.

### Task 4: Deploy and validate a real resource workflow

**Files:**
- Remote create: `/etc/agentos/node.json`

- [ ] **Step 1: Deploy and restart agentd when applicable**

Copy the updated package to `/usr/local/lib/agentd/agentd/`. Under native PID 1, restart only the server implementation if needed without stopping native services; no machine reboot is required.

- [ ] **Step 2: Run the resource workflow**

Submit a workflow with these steps:

1. `file.write` path `/etc/agentos/node.json`, content `{"role":"agent-native","version":"0.3.0"}`.
2. `file.read` with `expect` containing the same content string.
3. `package.info` for `bash`, with `expect={"installed":true}`.
4. `network.status`.
5. `system.status`.

Expected: workflow and associated task succeed; file content matches; backup metadata is present; network and SSH remain available.

- [ ] **Step 3: Verify file list/delete and final health**

Use `file.list` on `/etc/agentos`, then create and delete a temporary `/etc/agentos/resource-check.tmp`. Expected: delete returns `deleted:true` and a backup path. Confirm heartbeat, native network and SSH remain active.
