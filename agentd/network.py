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
