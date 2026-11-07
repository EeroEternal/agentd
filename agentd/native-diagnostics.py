#!/usr/bin/python
import json
import subprocess
import time
from pathlib import Path

from agentd.__main__ import request

OUTPUT = Path("/var/log/agentd/native-diagnostics.log")


def run(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return {"argv": argv, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except Exception as error:
        return {"argv": argv, "error": str(error)}


def main():
    time.sleep(20)
    report = {
        "commands": [
            run(["/usr/bin/ip", "-brief", "link"]),
            run(["/usr/bin/ip", "-brief", "address"]),
            run(["/usr/bin/nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]),
            run(["/usr/bin/nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT", "connection", "show"]),
            run(["/usr/bin/ps", "-eo", "pid,ppid,stat,comm,args"]),
        ],
        "native_services": request("native.list", {}),
        "system_status": request("system.status", {}),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    OUTPUT.write_text(text, encoding="utf-8")
    print(text, flush=True)


if __name__ == "__main__":
    main()
