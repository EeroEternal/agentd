import importlib.util
import json
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

from . import cell, recovery, vm

NATIVE_INIT = Path("/usr/local/lib/agentd/native-init.py")
TOKEN = Path("/etc/agentd/gateway.token")
LLM_CONFIG = Path("/etc/agentd/llm.json")
MCP_CONFIG = Path("/etc/agentd/mcp.json")
WORKSPACE = Path("/var/lib/agentos/workspace")


def executable(path):
    return path.is_file() and bool(path.stat().st_mode & 0o111)


def doctor():
    checks = {}
    checks["native_init_executable"] = executable(NATIVE_INIT)
    checks["cgroup_v2"] = cell.cgroup_ready()
    checks["cell_ready"] = cell.status()["ready"]
    checks["chromium"] = shutil.which("chromium") is not None
    checks["playwright"] = importlib.util.find_spec("playwright") is not None
    checks["mcp_config"] = MCP_CONFIG.is_file()
    checks["llm_config"] = LLM_CONFIG.is_file() and stat.S_IMODE(LLM_CONFIG.stat().st_mode) == 0o600
    checks["gateway_token"] = TOKEN.is_file() and stat.S_IMODE(TOKEN.stat().st_mode) == 0o600
    checks["kvm_ready"] = vm.status()["ready"]
    checks["network_interface"] = any(
        path.name != "lo" and (path / "operstate").read_text().strip() == "up"
        for path in Path("/sys/class/net").iterdir() if (path / "operstate").exists()
    )
    if checks["cell_ready"]:
        result = cell.execute(["python3", "-c", "import ctypes; print('ctypes-ok')"], 15)
        checks["cell_python_ctypes"] = result["exit_code"] == 0 and "ctypes-ok" in result["stdout"]
    else:
        checks["cell_python_ctypes"] = False
    required = [
        "native_init_executable", "cgroup_v2", "cell_ready", "cell_python_ctypes",
        "chromium", "playwright", "mcp_config", "llm_config", "gateway_token",
        "network_interface",
    ]
    return {
        "ready": all(checks[name] for name in required),
        "checks": checks,
        "default_entry": recovery.current_default(),
        "kvm_optional": checks["kvm_ready"],
    }


def pair():
    if not TOKEN.exists():
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    TOKEN.chmod(0o600)
    token = TOKEN.read_text(encoding="utf-8").strip()
    return {
        "host": "192.168.3.11",
        "pair_command": f"a pair {token}",
        "token_file": str(TOKEN),
    }


def install():
    if not shutil.which("useradd") and not shutil.which("id"):
        raise RuntimeError("user management tools are missing")
    if subprocess.run(["/usr/bin/id", "agentcell"], capture_output=True).returncode:
        subprocess.run([
            "/usr/bin/useradd", "--system", "--home", "/var/lib/agentos/cell-home",
            "--create-home", "--shell", "/usr/bin/nologin", "agentcell",
        ], check=True)
    for directory in (WORKSPACE, Path("/var/lib/agentos/tasks"), Path("/var/lib/agentos/vms")):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.chown(WORKSPACE, user="agentcell", group="agentcell")
    if NATIVE_INIT.exists():
        NATIVE_INIT.chmod(0o755)
    packaged_init = Path("/usr/local/lib/agentd/agentd/native-init.py")
    if packaged_init.exists():
        packaged_init.chmod(0o755)
    return {"installed": True, "doctor": doctor()}


def update():
    source = Path("/usr/local/src/agentd")
    if not (source / ".git").exists():
        raise RuntimeError(f"source checkout is missing: {source}")
    process = subprocess.run(["/usr/bin/git", "-C", str(source), "pull", "--ff-only"], capture_output=True, text=True, timeout=120)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "git update failed")
    for filename in Path(source / "agentd").glob("*.py"):
        shutil.copy2(filename, Path("/usr/local/lib/agentd/agentd") / filename.name)
    for filename in ("native-init.py", "native-console.py", "native-diagnostics.py"):
        shutil.copy2(source / "agentd" / filename, Path("/usr/local/lib/agentd") / filename)
    for filename in ("agentd_gateway.py", "agentd_webserver.py"):
        shutil.copy2(source / filename, Path("/usr/local/lib/agentd") / filename)
    for name in ("agentos", "a"):
        destination = Path("/usr/local/bin") / name
        shutil.copy2(source / "bin" / name, destination)
        destination.chmod(0o755)
    return {"updated": True, "install": install()}


def recover():
    if NATIVE_INIT.exists():
        NATIVE_INIT.chmod(0o755)
    result = recovery.set_default("Omarchy/linux")
    return {"recovered": True, **result}
