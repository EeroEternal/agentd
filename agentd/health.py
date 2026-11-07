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
