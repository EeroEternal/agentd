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
        *(f"--property={property_name}" for property_name in properties),
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
