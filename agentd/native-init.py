#!/usr/bin/python
import ctypes
import os
import signal
import shutil
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
    ("cgroup2", "/sys/fs/cgroup", "cgroup2", "nosuid,nodev,noexec"),
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
    if not mounted("/boot"):
        subprocess.run(["/usr/bin/mount", "/dev/sda1", "/boot"], check=True)
    for subvolume, target in (
        ("@home", "/home"),
        ("@log", "/var/log"),
        ("@pkg", "/var/cache/pacman/pkg"),
    ):
        Path(target).mkdir(parents=True, exist_ok=True)
        if not mounted(target):
            subprocess.run([
                "/usr/bin/mount", "-o", f"subvol={subvolume}",
                "/dev/mapper/root", target,
            ], check=True)
    for directory in (
        "/run/agentd", "/run/dbus", "/run/sshd", "/run/udev",
        "/run/systemd/resolve", "/run/agentos",
    ):
        Path(directory).mkdir(parents=True, exist_ok=True)
    shutil.chown("/run/systemd/resolve", user="systemd-resolve", group="systemd-resolve")
    Path("/run/agentos/native").touch()


def shutdown(signum, frame):
    print(f"agentos native: signal {signum}, stopping services", flush=True)
    SUPERVISOR.stop_all()
    os.sync()
    libc = ctypes.CDLL(None)
    command = 0x01234567 if signum == signal.SIGUSR1 else 0x4321fedc
    libc.syscall(169, 0xfee1dead, 672274793, command, 0)
    while True:
        time.sleep(3600)


def main():
    print("agentos native: initializing PID 1", flush=True)
    initialize()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGUSR1, shutdown)
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    SUPERVISOR.start_enabled()
    print("agentos native: triggering hardware coldplug", flush=True)
    subprocess.run(["/usr/bin/udevadm", "trigger", "--action=add"], check=True)
    subprocess.run(["/usr/bin/udevadm", "settle"], check=True)
    print("agentos native: initialization complete", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
