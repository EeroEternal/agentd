import os
import pwd
import uuid
from pathlib import Path

from .executor import execute as logged_execute

SAND = Path("/usr/local/bin/sand")
PYTHON = Path("/usr/bin/python")
LAUNCHER = Path("/usr/local/lib/agentd/agentd/cell_launcher.py")
ROOTFS = Path("/var/lib/agentos/cell-root")
WORKSPACE = Path("/var/lib/agentos/workspace")
CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_PARENT = CGROUP_ROOT / "agentos-cells"
CONTROLLERS = {"cpu", "memory", "pids"}
MEMORY = "1G"
CPU_CORES = 1
PIDS = 128


def cgroup_ready():
    controllers = CGROUP_ROOT / "cgroup.controllers"
    return controllers.is_file() and CONTROLLERS <= set(controllers.read_text().split())


def status():
    requirements = {
        "sand": SAND.is_file(),
        "launcher": LAUNCHER.is_file(),
        "rootfs": ROOTFS.is_dir(),
        "workspace": WORKSPACE.is_dir(),
        "cgroup_v2": cgroup_ready(),
        "root_privilege": os.geteuid() == 0,
    }
    return {
        "backend": "agentcell",
        "ready": all(requirements.values()),
        "requirements": requirements,
        "network": "none",
        "user": "agentcell",
    }


def enable_controllers(path):
    target = path / "cgroup.subtree_control"
    enabled = set(target.read_text().split()) if target.exists() else set()
    missing = CONTROLLERS - enabled
    if missing:
        target.write_text(" ".join(f"+{name}" for name in sorted(missing)), encoding="utf-8")


def prepare_group():
    CGROUP_PARENT.mkdir(exist_ok=True)
    enable_controllers(CGROUP_ROOT)
    enable_controllers(CGROUP_PARENT)
    group = CGROUP_PARENT / f"run-{uuid.uuid4().hex}.service"
    group.mkdir()
    enable_controllers(group)
    launcher = group / "launcher"
    launcher.mkdir()
    account = pwd.getpwnam("agentcell")
    for root, directories, files in os.walk(group):
        for name in [".", *directories, *files]:
            os.chown(Path(root) / name, account.pw_uid, account.pw_gid)
    return launcher, account.pw_uid, account.pw_gid


def cleanup_group(group):
    if not isinstance(group, Path) or group.parent.parent != CGROUP_PARENT:
        return
    root = group.parent
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass
    try:
        root.rmdir()
    except (FileNotFoundError, OSError):
        pass


def execute(argv, timeout=60):
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv must be a non-empty string array")
    if not all(isinstance(item, str) and item for item in argv):
        raise ValueError("argv must be a non-empty string array")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be between 1 and 3600 seconds")
    if not 1 <= timeout <= 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    state = status()
    if not state["ready"]:
        missing = [name for name, present in state["requirements"].items() if not present]
        raise RuntimeError(f"AgentCell is not ready: missing {', '.join(missing)}")
    group = None
    try:
        group, uid, gid = prepare_group()
        command = [
            str(PYTHON),
            str(LAUNCHER),
            "--group",
            str(group),
            "--uid",
            str(uid),
            "--gid",
            str(gid),
            "--",
            str(SAND),
            "--rootfs",
            str(ROOTFS),
            "--workdir",
            str(WORKSPACE),
            "--mem",
            MEMORY,
            "--cpu",
            str(CPU_CORES),
            "--pids",
            str(PIDS),
            "--net",
            "none",
            "--timeout",
            str(int(timeout)),
            "--",
            *argv,
        ]
        result = logged_execute(command, timeout + 5, cwd=str(WORKSPACE))
    finally:
        cleanup_group(group)
    result["backend"] = "agentcell"
    result["isolation"] = {
        "user": "agentcell",
        "network": "none",
        "rootfs": str(ROOTFS),
        "cgroup": str(group),
        "memory": MEMORY,
        "cpu_cores": CPU_CORES,
        "pids": PIDS,
    }
    return result
