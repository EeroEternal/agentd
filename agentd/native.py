import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFINITIONS = Path("/etc/agentd/services")
LOGS = Path("/var/log/agentd")
NAME = re.compile(r"^agentos-[a-zA-Z0-9_.@-]+$")
RESTART_POLICIES = {"no", "always", "on-failure", "on-abnormal"}


def now():
    return datetime.now(timezone.utc).isoformat()


class NativeSupervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.services = {}
        DEFINITIONS.mkdir(parents=True, exist_ok=True, mode=0o700)
        LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _name(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("native service name must use the agentos- prefix")
        return name

    def _path(self, name):
        return DEFINITIONS / f"{self._name(name)}.json"

    def _definition(self, name):
        return json.loads(self._path(name).read_text(encoding="utf-8"))

    def _write_definition(self, definition):
        target = self._path(definition["name"])
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    def install(self, params):
        name = self._name(params.get("name"))
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty string array")
        if not argv[0].startswith("/") or not Path(argv[0]).is_file():
            raise ValueError("argv[0] must be an absolute executable path")
        restart = params.get("restart", "on-failure")
        if restart not in RESTART_POLICIES:
            raise ValueError("invalid restart policy")
        restart_sec = params.get("restart_sec", 1)
        if isinstance(restart_sec, bool) or not isinstance(restart_sec, int) or not 0 <= restart_sec <= 60:
            raise ValueError("restart_sec must be between 0 and 60")
        priority = params.get("priority", 100)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
            raise ValueError("priority must be between 0 and 1000")
        enabled = params.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        tty = params.get("tty")
        if tty is not None and (not isinstance(tty, str) or not Path(tty).is_char_device()):
            raise ValueError("tty must be a character device path")
        depends_on = params.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            raise ValueError("depends_on must be a string array")
        for dependency in depends_on:
            self._name(dependency)
        start_timeout = params.get("start_timeout", 30)
        if isinstance(start_timeout, bool) or not isinstance(start_timeout, int) or not 1 <= start_timeout <= 300:
            raise ValueError("start_timeout must be between 1 and 300 seconds")
        ready = params.get("ready")
        if ready is not None:
            if not isinstance(ready, dict) or not isinstance(ready.get("argv"), list):
                raise ValueError("ready must contain an argv array")
            interval = ready.get("interval", 1)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.1 <= interval <= 10:
                raise ValueError("ready interval must be between 0.1 and 10 seconds")
        cleanup_paths = params.get("cleanup_paths", [])
        if not isinstance(cleanup_paths, list) or not all(
            isinstance(path, str) and path.startswith("/") for path in cleanup_paths
        ):
            raise ValueError("cleanup_paths must be absolute file paths")
        definition = {
            "name": name,
            "argv": argv,
            "restart": restart,
            "restart_sec": restart_sec,
            "priority": priority,
            "enabled": enabled,
            "tty": tty,
            "depends_on": depends_on,
            "start_timeout": start_timeout,
            "ready": ready,
            "cleanup_paths": cleanup_paths,
        }
        self._write_definition(definition)
        with self.lock:
            if name in self.services:
                service = self.services[name]
                service["definition"] = definition
                process = service["process"]
                restart_required = process is not None and process.poll() is None
            else:
                self.services[name] = {
                    "definition": definition,
                    "process": None,
                    "monitor": None,
                    "status": "stopped",
                    "pid": 0,
                    "restarts": 0,
                    "exit_code": None,
                    "started_at": None,
                    "stop_requested": False,
                }
                restart_required = False
        return {**definition, "restart_required": restart_required}

    def start(self, name):
        return self._start_with_dependencies(name, set())

    def _start_with_dependencies(self, name, seen):
        name = self._name(name)
        if name in seen:
            raise ValueError(f"dependency cycle detected at {name}")
        seen.add(name)
        if name not in self.services:
            self.install({"name": name, **self._definition(name)})
        for dependency in self.services[name]["definition"].get("depends_on", []):
            self._start_with_dependencies(dependency, seen)
        result = self._start_process(name)
        try:
            return self._wait_ready(name)
        except Exception:
            self.stop(name)
            raise

    def _wait_ready(self, name):
        service = self.services[name]
        definition = service["definition"]
        process = service["process"]
        ready = definition.get("ready")
        if ready is None:
            return self._status(service)
        deadline = time.monotonic() + definition["start_timeout"]
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited before becoming ready")
            result = subprocess.run(ready["argv"], capture_output=True, timeout=10)
            if result.returncode == 0:
                return self._status(service)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name} did not become ready")
            time.sleep(ready.get("interval", 1))

    def _start_process(self, name):
        name = self._name(name)
        with self.lock:
            service = self.services.get(name)
            if service is None:
                self.install({"name": name, **self._definition(name)})
                service = self.services[name]
            process = service["process"]
            if process is not None and process.poll() is None:
                return self._status(service)
            definition = service["definition"]
            for cleanup_path in definition.get("cleanup_paths", []):
                Path(cleanup_path).unlink(missing_ok=True)
            tty = definition.get("tty")
            if tty:
                tty_input = open(tty, "rb")
                tty_output = open(tty, "wb")
                stdin = tty_input
                stdout = tty_output
                log = None
            else:
                stdin = subprocess.DEVNULL
                log_path = LOGS / f"{name}.log"
                log = log_path.open("ab")
                stdout = log
            process = subprocess.Popen(
                definition["argv"],
                stdin=stdin,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if log is not None:
                log.close()
            if tty:
                tty_input.close()
                tty_output.close()
            service.update({
                "process": process,
                "status": "running",
                "pid": process.pid,
                "exit_code": None,
                "started_at": now(),
                "stop_requested": False,
            })
            monitor = threading.Thread(target=self._monitor, args=(name, process), daemon=True)
            service["monitor"] = monitor
            monitor.start()
            return self._status(service)

    def _monitor(self, name, process):
        exit_code = process.wait()
        with self.lock:
            service = self.services[name]
            if service["process"] is not process:
                return
            service["status"] = "stopped"
            service["exit_code"] = exit_code
            stop_requested = service["stop_requested"]
            definition = service["definition"]
        restart = definition["restart"]
        should_restart = not stop_requested and (
            restart == "always"
            or (restart == "on-failure" and exit_code != 0)
            or (restart == "on-abnormal" and exit_code < 0)
        )
        if should_restart:
            with self.lock:
                service["restarts"] += 1
                service["status"] = "restarting"
            time.sleep(definition["restart_sec"])
            self.start(name)
            for dependent in self._running_dependents(name):
                self.restart(dependent)

    def _running_dependents(self, name):
        with self.lock:
            return [
                service_name
                for service_name, service in self.services.items()
                if name in service["definition"].get("depends_on", [])
                and service["process"] is not None
                and service["process"].poll() is None
            ]

    def stop(self, name):
        name = self._name(name)
        with self.lock:
            service = self.services.get(name)
            if service is None:
                self.install({"name": name, **self._definition(name)})
                service = self.services[name]
            process = service["process"]
            service["stop_requested"] = True
            monitor = service["monitor"]
        if process is None or process.poll() is not None:
            service["status"] = "stopped"
            return self._status(service)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=5)
        with self.lock:
            service["status"] = "stopped"
        return self._status(service)

    def restart(self, name):
        self.stop(name)
        return self.start(name)

    def _status(self, service):
        return {
            "name": service["definition"]["name"],
            "status": service["status"],
            "pid": service["pid"],
            "restarts": service["restarts"],
            "exit_code": service["exit_code"],
            "started_at": service["started_at"],
        }

    def status(self, name):
        name = self._name(name)
        with self.lock:
            if name not in self.services:
                self.install({"name": name, **self._definition(name)})
            return self._status(self.services[name])

    def logs(self, name, lines=20):
        name = self._name(name)
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 200:
            raise ValueError("lines must be between 1 and 200")
        path = LOGS / f"{name}.log"
        if not path.exists():
            return {"name": name, "logs": ""}
        content = path.read_text(encoding="utf-8", errors="replace")
        return {"name": name, "logs": "\n".join(content.splitlines()[-lines:])}

    def list(self):
        names = {path.stem for path in DEFINITIONS.glob("*.json")}
        return [self.status(name) for name in sorted(names)]

    def start_enabled(self):
        definitions = [json.loads(path.read_text(encoding="utf-8")) for path in DEFINITIONS.glob("*.json")]
        definitions.sort(key=lambda definition: definition["priority"])
        print(f"agentos native: found {len(definitions)} service definitions", flush=True)
        for definition in definitions:
            if definition["enabled"]:
                try:
                    status = self.start(definition["name"])
                    print(f"agentos native: started {definition['name']} pid={status['pid']}", flush=True)
                except Exception as error:
                    print(f"agentos native: failed to start {definition['name']}: {error}", flush=True)

    def stop_all(self):
        definitions = [json.loads(path.read_text(encoding="utf-8")) for path in DEFINITIONS.glob("*.json")]
        definitions.sort(key=lambda definition: definition["priority"], reverse=True)
        for definition in definitions:
            try:
                self.stop(definition["name"])
            except Exception:
                pass


SUPERVISOR = NativeSupervisor()
