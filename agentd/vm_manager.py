import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path

QEMU = Path("/usr/bin/qemu-system-x86_64")
QEMU_IMG = Path("/usr/bin/qemu-img")
BASE_IMAGE = Path("/var/lib/agentos/guest-agent-base.raw")
KERNEL = Path("/boot/6e5d19fd7113410e935423dde66fb804/linux/vmlinuz")
VMS = Path("/var/lib/agentos/vms")
RUNTIME = Path("/run/agentos/vms")
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")


def validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("VM name must start with a letter and contain only lowercase letters, digits, and hyphens")
    return name


def paths(name):
    validate_name(name)
    return {
        "disk": VMS / f"{name}.qcow2",
        "metadata": VMS / f"{name}.json",
        "runtime": RUNTIME / name,
        "pidfile": RUNTIME / name / "qemu.pid",
        "serial": RUNTIME / name / "serial.log",
        "monitor": RUNTIME / name / "monitor.sock",
        "agent": RUNTIME / name / "agent.sock",
    }


def running(name):
    pidfile = paths(name)["pidfile"]
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return None


def status(name):
    state = paths(name)
    pid = running(name)
    return {
        "name": name,
        "exists": state["disk"].is_file(),
        "running": pid is not None,
        "pid": pid,
        "disk": str(state["disk"]),
        "snapshot_safe": pid is None,
    }


def create(name, memory=512, cpus=1):
    state = paths(name)
    if state["disk"].exists():
        raise ValueError(f"VM already exists: {name}")
    if not BASE_IMAGE.is_file():
        raise RuntimeError(f"Guest base image is missing: {BASE_IMAGE}")
    if not KERNEL.is_file():
        raise RuntimeError(f"Guest kernel is missing: {KERNEL}")
    if isinstance(memory, bool) or not isinstance(memory, int) or not 128 <= memory <= 4096:
        raise ValueError("memory must be between 128 and 4096 MiB")
    if isinstance(cpus, bool) or not isinstance(cpus, int) or not 1 <= cpus <= 4:
        raise ValueError("cpus must be between 1 and 4")
    VMS.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(QEMU_IMG), "create", "-f", "qcow2", "-F", "raw", "-b", str(BASE_IMAGE), str(state["disk"]),
    ], check=True, capture_output=True, text=True, timeout=30)
    state["metadata"].write_text(json.dumps({"name": name, "memory": memory, "cpus": cpus}) + "\n")
    return status(name)


def qemu_command(name):
    state = paths(name)
    metadata = json.loads(state["metadata"].read_text())
    return [
        str(QEMU),
        "-enable-kvm",
        "-cpu",
        "host",
        "-m",
        str(metadata["memory"]),
        "-smp",
        str(metadata["cpus"]),
        "-kernel",
        str(KERNEL),
        "-drive",
        f"file={state['disk']},format=qcow2,if=virtio",
        "-append",
        "root=/dev/vda rw console=ttyS0 init=/sbin/init",
        "-display",
        "none",
        "-serial",
        f"file:{state['serial']}",
        "-monitor",
        f"unix:{state['monitor']},server=on,wait=off",
        "-chardev",
        f"socket,path={state['agent']},server=on,wait=off,id=agent",
        "-device",
        "virtio-serial",
        "-device",
        "virtserialport,chardev=agent,name=agentos",
        "-net",
        "none",
        "-daemonize",
        "-pidfile",
        str(state["pidfile"]),
    ]


def start(name):
    if running(name):
        return status(name)
    state = paths(name)
    if not state["disk"].is_file():
        raise ValueError(f"VM does not exist: {name}")
    state["runtime"].mkdir(parents=True, exist_ok=True)
    subprocess.run(qemu_command(name), check=True, capture_output=True, text=True, timeout=20)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if state["agent"].exists():
            return status(name)
        time.sleep(0.1)
    raise RuntimeError(f"VM {name} did not expose its guest agent")


def request(name, method, params=None, timeout=30):
    state = paths(name)
    request_id = uuid.uuid4().hex
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(state["agent"]))
        client.sendall(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
        }).encode() + b"\n")
        response = client.makefile("rb").readline(2_097_153)
    if not response or len(response) > 2_097_152:
        raise RuntimeError("invalid guest agent response")
    result = json.loads(response)
    if result.get("id") != request_id:
        raise RuntimeError("guest agent response id mismatch")
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "guest agent request failed"))
    return result["result"]


def execute(name, argv, timeout=60):
    if not running(name):
        start(name)
    return request(name, "exec", {"argv": argv, "timeout": timeout}, timeout + 10)


def stop(name):
    state = paths(name)
    pid = running(name)
    if pid is None:
        return status(name)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(state["monitor"]))
            client.sendall(b"quit\n")
    except (FileNotFoundError, ConnectionError, socket.timeout):
        os.kill(pid, 15)
    deadline = time.monotonic() + 10
    while running(name) and time.monotonic() < deadline:
        time.sleep(0.1)
    if running(name):
        raise RuntimeError(f"VM {name} did not stop")
    return status(name)


def snapshot(name, snapshot_name):
    validate_name(snapshot_name)
    if running(name):
        raise ValueError("stop the VM before creating a snapshot")
    state = paths(name)
    subprocess.run([str(QEMU_IMG), "snapshot", "-c", snapshot_name, str(state["disk"])], check=True, capture_output=True, text=True, timeout=30)
    return {"name": name, "snapshot": snapshot_name, "created": True}


def restore(name, snapshot_name):
    validate_name(snapshot_name)
    if running(name):
        raise ValueError("stop the VM before restoring a snapshot")
    state = paths(name)
    subprocess.run([str(QEMU_IMG), "snapshot", "-a", snapshot_name, str(state["disk"])], check=True, capture_output=True, text=True, timeout=30)
    return {"name": name, "snapshot": snapshot_name, "restored": True}


def delete(name):
    state = paths(name)
    stop(name)
    if state["disk"].exists():
        state["disk"].unlink()
    if state["metadata"].exists():
        state["metadata"].unlink()
    if state["runtime"].exists():
        for path in state["runtime"].iterdir():
            path.unlink()
        state["runtime"].rmdir()
    return {"name": name, "deleted": True}


def smoke():
    name = f"smoke-{uuid.uuid4().hex[:8]}"
    try:
        create(name)
        start(name)
        guest = request(name, "status")
        result = execute(name, ["uname", "-r"])
        stop(name)
        return {
            "name": name,
            "guest": guest,
            "exec": result,
            "marker": "AGENTOS_VM_SMOKE_OK",
        }
    finally:
        if status(name)["exists"]:
            delete(name)
