# AgentOS Physical Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a root local executor on the Omarchy laptop, build an independent `7.1.9-agent1` kernel, and prepare a manually selectable Limine boot entry without replacing the working Omarchy kernel.

**Architecture:** The Mac-side Agent drives `admin@192.168.3.11` over SSH. A Python standard-library `agentd` executes argv-based commands as root through a root-only Unix socket and persists run logs under `/srv/agentos/runs`. Kernel artifacts and boot configuration are changed only after each preceding build stage succeeds.

**Tech Stack:** Python 3 standard library, systemd, Arch Linux, Linux kernel Kbuild, mkinitcpio, Btrfs/Snapper, Limine.

---

## File map

- `agentd/__init__.py`: package marker.
- `agentd/executor.py`: subprocess lifecycle, timeout handling, and persistent run logs.
- `agentd/server.py`: root-only Unix socket JSON-lines server.
- `agentd/__main__.py`: server/client command-line entry point.
- `systemd/agentd.service`: unrestricted root systemd unit with a private runtime directory.
- Remote `/srv/agentos/src/linux`: Arch Linux kernel source at `v7.1.9-arch1`.
- Remote `/srv/agentos/build/agent1`: out-of-tree kernel build.
- Remote `/srv/agentos/artifacts/agent1`: validated kernel artifacts and boot-configuration backup.
- Remote `/boot/agentos/agent1`: test kernel and initramfs.
- Remote `/boot/limine.conf`: receives one appended top-level `AgentOS Test` entry; existing content and default remain unchanged.

### Task 1: Implement the logged command executor

**Files:**
- Create: `agentd/__init__.py`
- Create: `agentd/executor.py`

- [ ] **Step 1: Create the package marker**

```python
__all__ = []
```

- [ ] **Step 2: Implement argv execution, process-group timeout, full logs, and bounded replies**

```python
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

RUNS = Path("/srv/agentos/runs")
MAX_REPLY_BYTES = 65_536


def _save_stream(stream, path):
    stream.flush()
    stream.seek(0)
    with path.open("wb") as target:
        shutil.copyfileobj(stream, target)


def _read_tail(stream):
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - MAX_REPLY_BYTES))
    return stream.read().decode("utf-8", errors="replace")


def execute(argv, timeout, cwd=None):
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv must be a non-empty string array")
    if not all(isinstance(item, str) and item for item in argv):
        raise ValueError("argv must be a non-empty string array")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be between 1 and 86400 seconds")
    if not 1 <= timeout <= 86400:
        raise ValueError("timeout must be between 1 and 86400 seconds")

    working_directory = cwd or "/srv/agentos"
    if not isinstance(working_directory, str) or not Path(working_directory).is_dir():
        raise ValueError("cwd must be an existing directory")

    run_id = uuid.uuid4().hex
    RUNS.mkdir(parents=True, exist_ok=True)
    stdout_log = RUNS / f"{run_id}.stdout"
    stderr_log = RUNS / f"{run_id}.stderr"
    started = time.monotonic()

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            argv,
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exit_code = process.wait()

        _save_stream(stdout, stdout_log)
        _save_stream(stderr, stderr_log)
        result = {
            "run_id": run_id,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _read_tail(stdout),
            "stderr": _read_tail(stderr),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
        }

    metadata = {
        "run_id": run_id,
        "argv": argv,
        "cwd": working_directory,
        "timeout": timeout,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": result["duration_seconds"],
    }
    (RUNS / f"{run_id}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
```

- [ ] **Step 3: Check syntax and verify success and failure behavior without adding test files**

Run:

```bash
python3 -m py_compile agentd/__init__.py agentd/executor.py
python3 -c 'from pathlib import Path; import agentd.executor as executor; executor.RUNS=Path("/tmp/agentd-plan-runs"); ok=executor.execute(["/bin/sh","-c","exit 0"],5,"/tmp"); bad=executor.execute(["/bin/sh","-c","exit 7"],5,"/tmp"); assert ok["exit_code"] == 0; assert bad["exit_code"] == 7; assert Path(ok["stdout_log"]).exists()'
```

Expected: exit status `0` and no output.

### Task 2: Implement the Unix socket server and CLI

**Files:**
- Create: `agentd/server.py`
- Create: `agentd/__main__.py`

- [ ] **Step 1: Implement the JSON-lines server**

```python
import json
import os
import socketserver
from pathlib import Path

from .executor import execute

SOCKET_PATH = Path("/run/agentd/agentd.sock")
MAX_REQUEST_BYTES = 1_048_576


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request_id = None
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise ValueError("request is empty or too large")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            if request.get("method") != "exec":
                raise ValueError("method must be exec")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = execute(
                params.get("argv"),
                params.get("timeout", 60),
                params.get("cwd"),
            )
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve():
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        if not SOCKET_PATH.is_socket():
            raise RuntimeError(f"refusing to replace non-socket: {SOCKET_PATH}")
        SOCKET_PATH.unlink()
    old_umask = os.umask(0o177)
    try:
        with Server(str(SOCKET_PATH), Handler) as server:
            os.umask(old_umask)
            server.serve_forever()
    finally:
        os.umask(old_umask)
        SOCKET_PATH.unlink(missing_ok=True)
```

- [ ] **Step 2: Implement the CLI**

```python
import json
import socket
import sys
import uuid

from .server import SOCKET_PATH, serve


def request(argv, timeout):
    payload = {
        "id": uuid.uuid4().hex,
        "method": "exec",
        "params": {"argv": argv, "timeout": timeout},
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(max(timeout + 5, 10))
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode() + b"\n")
        response = client.makefile("rb").readline(2_097_153)
    if not response or len(response) > 2_097_152:
        raise RuntimeError("invalid response")
    return json.loads(response)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "server":
        serve()
        return 0
    if len(sys.argv) >= 5 and sys.argv[1] == "exec" and sys.argv[2] == "--timeout":
        response = request(sys.argv[4:], int(sys.argv[3]))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") and response["result"]["exit_code"] == 0 else 1
    print("usage: python -m agentd server | python -m agentd exec --timeout SECONDS ARGV...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Check syntax and CLI usage**

Run:

```bash
python3 -m py_compile agentd/server.py agentd/__main__.py
python3 -m agentd
```

Expected: syntax check succeeds; usage command exits `2` and prints the server/exec usage line.

### Task 3: Package and install agentd on Omarchy

**Files:**
- Create: `systemd/agentd.service`
- Remote create: `/usr/local/lib/agentd/agentd/`
- Remote create: `/etc/systemd/system/agentd.service`

- [ ] **Step 1: Create the systemd unit**

```ini
[Unit]
Description=AgentOS Privileged Executor
After=local-fs.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/usr/local/lib/agentd
Environment=PYTHONPATH=/usr/local/lib/agentd
ExecStart=/usr/bin/python -m agentd server
Restart=on-failure
RuntimeDirectory=agentd
RuntimeDirectoryMode=0700
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Prepare remote directories and copy only the new project files**

Run from the workspace:

```bash
ssh admin@192.168.3.11 'mkdir -p /tmp/agentd-install'
scp -r agentd admin@192.168.3.11:/tmp/agentd-install/
scp systemd/agentd.service admin@192.168.3.11:/tmp/agentd-install/
ssh admin@192.168.3.11 'sudo install -d -m 0755 /usr/local/lib/agentd /srv/agentos /srv/agentos/src /srv/agentos/build /srv/agentos/artifacts /srv/agentos/runs && sudo cp -a /tmp/agentd-install/agentd /usr/local/lib/agentd/ && sudo install -m 0644 /tmp/agentd-install/agentd.service /etc/systemd/system/agentd.service && sudo systemctl daemon-reload && sudo systemctl enable --now agentd'
```

Expected: `agentd` starts successfully. The remote sudo step uses the credentials supplied interactively through the established execution channel; no password is written to a file.

- [ ] **Step 3: Verify root execution, logging, and failure propagation**

Run:

```bash
ssh admin@192.168.3.11 'sudo /usr/bin/python -c '"'"'import json,socket,uuid; p={"id":uuid.uuid4().hex,"method":"exec","params":{"argv":["/usr/bin/id"],"timeout":5}}; s=socket.socket(socket.AF_UNIX); s.connect("/run/agentd/agentd.sock"); s.sendall(json.dumps(p).encode()+b"\n"); r=json.loads(s.makefile("rb").readline()); assert r["ok"] and "uid=0(root)" in r["result"]["stdout"]; print(r["result"]["stdout"],end="")'"'"''
ssh admin@192.168.3.11 'systemctl is-active agentd && sudo ls -l /run/agentd/agentd.sock /srv/agentos/runs'
```

Expected: first command prints `uid=0(root)`; second prints `active`, a socket with no world access, and run logs.

### Task 4: Snapshot Omarchy and install build prerequisites

**Files:**
- Remote create: Snapper pre-change snapshot
- Remote create: `/srv/agentos/artifacts/agent1/environment.txt`

- [ ] **Step 1: Record the current state and create a snapshot**

Run:

```bash
ssh admin@192.168.3.11 'sudo install -d -m 0755 /srv/agentos/artifacts/agent1 && { uname -a; pacman -Q linux limine mkinitcpio snapper; bootctl status 2>&1 || true; sudo cat /proc/cmdline; } | sudo tee /srv/agentos/artifacts/agent1/environment.txt && sudo snapper -c root create --description agentos-agent1-baseline'
```

Expected: a new numbered Snapper snapshot and an environment record containing `7.1.9-arch1-2`.

- [ ] **Step 2: Install only the required additional packages**

Run:

```bash
ssh admin@192.168.3.11 'sudo pacman -Sy --noconfirm && sudo pacman -S --needed --noconfirm bc pahole'
```

Expected: `pacman` exits `0`; existing `base-devel`, `git`, `flex`, `bison`, `openssl`, and `cpio` remain installed. Do not install the repository's newer `linux-headers` for this full-kernel build: after database refresh it is `7.2.11`, while the running kernel is `7.1.9`, and headers are not required to compile the complete kernel.

- [ ] **Step 3: Inspect actual initramfs configuration before building**

Run:

```bash
ssh admin@192.168.3.11 'ls -la /etc/mkinitcpio.conf /etc/mkinitcpio.conf.d /etc/mkinitcpio.d; sudo zcat /proc/config.gz | sudo tee /srv/agentos/artifacts/agent1/running.config >/dev/null; sudo test -s /srv/agentos/artifacts/agent1/running.config'
```

Expected: exit `0`. Read every preset/drop-in that applies. Confirm that the effective initramfs hooks support the current `cryptdevice` LUKS boot path; if they do not, stop and reconcile the configuration with the known-good initramfs before generating another one.

### Task 5: Build the baseline agent1 kernel

**Files:**
- Remote create: `/srv/agentos/src/linux/`
- Remote create: `/srv/agentos/build/agent1/`

- [ ] **Step 1: Fetch the Arch source tag matching the installed kernel**

Run:

Run on the Mac, then transfer over the LAN:

```bash
mkdir -p .agentos-tmp
curl --http1.1 --fail --location --retry 5 --retry-all-errors --continue-at - --output .agentos-tmp/v7.1.9-arch1.tar.gz https://codeload.github.com/archlinux/linux/tar.gz/refs/tags/v7.1.9-arch1
rsync -av .agentos-tmp/v7.1.9-arch1.tar.gz admin@192.168.3.11:/tmp/
ssh admin@192.168.3.11 'sudo rm -f /srv/agentos/src/v7.1.9-arch1.tar.gz && sudo mv /tmp/v7.1.9-arch1.tar.gz /srv/agentos/src/ && sudo rm -rf /srv/agentos/src/linux && sudo install -d -m 0755 /srv/agentos/src/linux && sudo tar -xzf /srv/agentos/src/v7.1.9-arch1.tar.gz -C /srv/agentos/src/linux --strip-components=1'
```

Expected: the exact `v7.1.9-arch1` source archive downloads on the Mac, transfers over the LAN, and extracts successfully. Git smart-protocol clones failed during transport, so the same tag is fetched as a tarball without substituting another version. If the tag is absent, stop rather than silently substituting another kernel version; retrieve the exact Arch `7.1.9.arch1-2` package source instead.

- [ ] **Step 2: Configure an independent out-of-tree build**

Run:

```bash
ssh admin@192.168.3.11 'sudo install -d -m 0755 /srv/agentos/build/agent1 && sudo cp /srv/agentos/artifacts/agent1/running.config /srv/agentos/build/agent1/.config && cd /srv/agentos/src/linux && sudo make O=/srv/agentos/build/agent1 EXTRAVERSION= olddefconfig && sudo scripts/config --file /srv/agentos/build/agent1/.config --set-str LOCALVERSION -agent1 && sudo make O=/srv/agentos/build/agent1 EXTRAVERSION= olddefconfig'
```

Expected: no configuration errors; `CONFIG_LOCALVERSION="-agent1"` in the final `.config`.

- [ ] **Step 3: Build through agentd with persistent logs**

Run locally over SSH as root:

```bash
ssh admin@192.168.3.11 'sudo env PYTHONPATH=/usr/local/lib/agentd /usr/bin/python -m agentd exec --timeout 28800 make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= -j2 bzImage modules'
```

Expected: JSON response has `ok:true`, `exit_code:0`, and `timed_out:false`. On compiler failure, preserve logs, fix the identified build issue, and do not proceed to installation.

- [ ] **Step 4: Verify the built release and image**

Run:

```bash
ssh admin@192.168.3.11 'sudo make -s -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= kernelrelease && sudo test -s /srv/agentos/build/agent1/arch/x86/boot/bzImage'
```

Expected: first line is exactly `7.1.9-agent1`; image check exits `0`.

### Task 6: Install modules and generate the test initramfs

**Files:**
- Remote create: `/usr/lib/modules/7.1.9-agent1/`
- Remote create: `/srv/agentos/artifacts/agent1/vmlinuz`
- Remote create: `/srv/agentos/artifacts/agent1/initramfs`
- Remote create: `/srv/agentos/artifacts/agent1/config`

- [ ] **Step 1: Install modules under the independent release name**

Run:

```bash
ssh admin@192.168.3.11 'sudo make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= modules_install && sudo cp /srv/agentos/build/agent1/arch/x86/boot/bzImage /srv/agentos/artifacts/agent1/vmlinuz && sudo cp /srv/agentos/build/agent1/.config /srv/agentos/artifacts/agent1/config && sudo test -d /usr/lib/modules/7.1.9-agent1'
```

Expected: modules are installed only under `7.1.9-agent1`; existing `7.1.9-arch1-2` modules remain present.

- [ ] **Step 2: Generate initramfs only after confirming effective hooks in Task 4**

Run:

```bash
ssh admin@192.168.3.11 'sudo mkinitcpio -k 7.1.9-agent1 -c /etc/mkinitcpio.conf -g /srv/agentos/artifacts/agent1/initramfs'
```

Expected: exit `0`, non-empty initramfs, and no missing `7.1.9-agent1` module errors. If effective hooks were split across drop-ins and are not applied, stop and supply a dedicated preset/config representing the known-good hooks rather than ignoring the discrepancy.

- [ ] **Step 3: Verify original boot files are intact**

Run:

```bash
ssh admin@192.168.3.11 'sudo test -s /boot/6e5d19fd7113410e935423dde66fb804/linux/vmlinuz && sudo test -s /boot/6e5d19fd7113410e935423dde66fb804/linux/initramfs && sudo test -s /srv/agentos/artifacts/agent1/vmlinuz && sudo test -s /srv/agentos/artifacts/agent1/initramfs'
```

Expected: all four files exist and are non-empty.

### Task 7: Add an independent Limine test entry

**Files:**
- Remote create: `/boot/agentos/agent1/vmlinuz`
- Remote create: `/boot/agentos/agent1/initramfs`
- Remote create: `/srv/agentos/artifacts/agent1/limine.conf.before`
- Remote modify: `/boot/limine.conf`

- [ ] **Step 1: Install boot artifacts and back up Limine outside the Btrfs root**

Run:

```bash
ssh admin@192.168.3.11 'sudo install -d -m 0755 /boot/agentos/agent1 && sudo install -m 0644 /srv/agentos/artifacts/agent1/vmlinuz /boot/agentos/agent1/vmlinuz && sudo install -m 0644 /srv/agentos/artifacts/agent1/initramfs /boot/agentos/agent1/initramfs && sudo cp /boot/limine.conf /srv/agentos/artifacts/agent1/limine.conf.before'
```

Expected: independent boot files and backup exist; original boot paths are untouched.

- [ ] **Step 2: Append a top-level entry without changing the original entry or `default_entry: 2`**

Append exactly this block to `/boot/limine.conf`:

```text

/+AgentOS Test
  //linux
  comment: Baseline AgentOS kernel 7.1.9-agent1
  protocol: linux
  path: boot():/agentos/agent1/vmlinuz
  module_path: boot():/agentos/agent1/initramfs
  cmdline: cryptdevice=PARTUUID=e553df7c-02:root root=/dev/mapper/root zswap.enabled=0 rootflags=subvol=@ rw rootfstype=btrfs loglevel=7 systemd.show_status=true
```

Use a local temporary copy and `sudo install` only after confirming the temporary copy contains the complete original file plus this single block. Do not run `limine-entry-tool` or `limine-snapper-sync` against this hand-written entry.

- [ ] **Step 3: Verify paths, uniqueness, and unchanged default**

Run:

```bash
ssh admin@192.168.3.11 'sudo python -c '"'"'from pathlib import Path; p=Path("/boot/limine.conf").read_text(); before=Path("/srv/agentos/artifacts/agent1/limine.conf.before").read_text(); assert p.startswith(before); assert p.count("/+AgentOS Test") == 1; assert "default_entry: 2" in p; assert Path("/boot/agentos/agent1/vmlinuz").stat().st_size > 0; assert Path("/boot/agentos/agent1/initramfs").stat().st_size > 0'"'"''
```

Expected: exit `0`. If the check fails, restore `/srv/agentos/artifacts/agent1/limine.conf.before` to `/boot/limine.conf` before any reboot.

### Task 8: Exercise the physical boot and return path

**Files:**
- Remote create after boot: `/srv/agentos/artifacts/agent1/boot-verification.txt`

- [ ] **Step 1: Confirm operator readiness and reboot without changing the default**

Tell the operator to select `AgentOS Test` at the Limine menu and unlock LUKS using the existing procedure. Reboot only after the operator confirms physical access to the laptop. Do not make `AgentOS Test` the default.

- [ ] **Step 2: Reconnect and record the new-kernel checks**

Run:

```bash
ssh admin@192.168.3.11 '{ uname -r; findmnt -no SOURCE,FSTYPE,OPTIONS /; systemctl is-system-running; systemctl is-active agentd; systemctl is-active sshd; lspci -nnk; } | sudo tee /srv/agentos/artifacts/agent1/boot-verification.txt'
```

Expected: `7.1.9-agent1`, the expected Btrfs root, `running`, active `agentd` and `sshd`, and loaded storage/network/display drivers. Do not claim success until SSH reconnects and these checks pass.

- [ ] **Step 3: Reboot to the original Omarchy entry and verify the return path**

The operator selects the original `Omarchy` entry. Reconnect and run:

```bash
ssh admin@192.168.3.11 'uname -r; systemctl is-system-running; systemctl is-active agentd'
```

Expected: `7.1.9-arch1-2`, `running`, and `active`. This completes the baseline milestone; any functional kernel patch is a separate milestone.
