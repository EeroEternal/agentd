#!/usr/bin/python3
import ctypes
import json
import os
import platform
import subprocess
import time
import uuid

PORT = "/dev/virtio-ports/agentos"
MAX_REPLY = 65_536


def execute(argv, timeout=60):
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("argv must be a non-empty string array")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be between 1 and 300 seconds")
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd="/")
        return {
            "exit_code": process.returncode,
            "stdout": process.stdout[-MAX_REPLY:],
            "stderr": process.stderr[-MAX_REPLY:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "exit_code": None,
            "stdout": (error.stdout or b"").decode() if isinstance(error.stdout, bytes) else error.stdout or "",
            "stderr": (error.stderr or b"").decode() if isinstance(error.stderr, bytes) else error.stderr or "",
            "timed_out": True,
        }


def poweroff():
    os.sync()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall(169, 0xFEE1DEAD, 672274793, 0x4321FEDC, 0)


def handle(request):
    method = request.get("method")
    if method == "status":
        return {"kernel": platform.release(), "machine": platform.machine(), "pid": os.getpid()}
    if method == "exec":
        return execute(request.get("params", {}).get("argv"), request.get("params", {}).get("timeout", 60))
    if method == "poweroff":
        return {"powering_off": True}
    raise ValueError("unknown method")


def find_port():
    if os.path.exists(PORT):
        return PORT
    parent = "/sys/class/virtio-ports"
    if os.path.isdir(parent):
        for entry in os.listdir(parent):
            try:
                with open(f"{parent}/{entry}/name") as source:
                    if source.read().strip() == "agentos":
                        device = f"/dev/{entry}"
                        if os.path.exists(device):
                            return device
            except FileNotFoundError:
                pass
    return PORT


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    for filesystem, target in (("proc", "/proc"), ("sysfs", "/sys"), ("devtmpfs", "/dev")):
        os.makedirs(target, exist_ok=True)
        if libc.mount(filesystem.encode(), target.encode(), filesystem.encode(), 0, None) != 0:
            error = ctypes.get_errno()
            if error != 16:
                raise OSError(error, os.strerror(error), target)
    device = PORT
    for _ in range(100):
        device = find_port()
        if device != PORT:
            break
        time.sleep(0.1)
    print("AGENTOS_GUEST_AGENT_READY", flush=True)
    with open(device, "r+b", buffering=0) as port:
        while True:
            line = port.readline(2_097_153)
            if not line:
                time.sleep(0.1)
                continue
            request_id = None
            try:
                request = json.loads(line)
                request_id = request.get("id")
                result = handle(request)
                response = {"id": request_id, "ok": True, "result": result}
            except Exception as error:
                response = {"id": request_id, "ok": False, "error": str(error)}
            port.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            if request_id and request.get("method") == "poweroff":
                poweroff()


if __name__ == "__main__":
    main()
