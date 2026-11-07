import json
import os
import socketserver
from pathlib import Path

from .executor import execute
from .store import TaskStore
from .systemd import action as service_action
from .systemd import install as service_install
from .systemd import logs as service_logs
from .systemd import status as service_status
from .systemd import system_status
from .workflows import WorkflowManager
from .native import SUPERVISOR as NATIVE
from .files import delete as file_delete
from .files import list_directory
from .files import read as file_read
from .files import restore as file_restore
from .files import write as file_write
from .network import status as network_status
from .packages import info as package_info
from .packages import install as package_install
from .packages import list_installed
from .packages import remove as package_remove
from .health import health as system_health

SOCKET_PATH = Path("/run/agentd/agentd.sock")
MAX_REQUEST_BYTES = 1_048_576
STORE = TaskStore()


TASK_METHODS = {"task.create", "task.get", "task.list", "task.complete", "task.fail"}


def dispatch(method, params):
    task_id = params.get("task_id")
    if task_id and method not in TASK_METHODS:
        task = STORE.get(task_id)
        if task["status"] in {"succeeded", "failed"}:
            raise ValueError("cannot execute operations for a completed task")
    try:
        if method == "exec":
            result = execute(params.get("argv"), params.get("timeout", 60), params.get("cwd"))
        elif method == "system.status":
            result = system_status()
        elif method == "service.install":
            result = service_install(params)
        elif method in {"service.start", "service.stop", "service.restart"}:
            result = service_action(params, method.split(".", 1)[1])
        elif method == "service.status":
            result = service_status(params)
        elif method == "service.logs":
            result = service_logs(params)
        elif method == "task.create":
            result = STORE.create(params.get("goal"))
        elif method == "task.get":
            result = STORE.get(params.get("task_id"))
        elif method == "task.list":
            result = STORE.list()
        elif method == "task.complete":
            result = STORE.complete(params.get("task_id"))
        elif method == "task.fail":
            result = STORE.fail(params.get("task_id"), params.get("reason"))
        elif method == "workflow.start":
            result = WORKFLOWS.start(params.get("goal"), params.get("steps"))
        elif method == "workflow.get":
            result = WORKFLOWS.get(params.get("workflow_id"))
        elif method == "workflow.list":
            result = WORKFLOWS.list()
        elif method == "native.install":
            result = NATIVE.install(params)
        elif method == "native.start":
            result = NATIVE.start(params.get("name"))
        elif method == "native.stop":
            result = NATIVE.stop(params.get("name"))
        elif method == "native.restart":
            result = NATIVE.restart(params.get("name"))
        elif method == "native.status":
            result = NATIVE.status(params.get("name"))
        elif method == "native.logs":
            result = NATIVE.logs(params.get("name"), params.get("lines", 20))
        elif method == "native.list":
            result = NATIVE.list()
        elif method == "system.health":
            result = system_health()
        elif method == "file.read":
            result = file_read(params)
        elif method == "file.restore":
            result = file_restore(params)
        elif method == "file.write":
            result = file_write(params)
        elif method == "file.list":
            result = list_directory(params)
        elif method == "file.delete":
            result = file_delete(params)
        elif method == "package.info":
            result = package_info(params)
        elif method == "package.list":
            result = list_installed()
        elif method == "package.install":
            result = package_install(params)
        elif method == "package.remove":
            result = package_remove(params)
        elif method == "network.status":
            result = network_status()
        else:
            raise ValueError("unknown method")
    except Exception as error:
        if task_id and method not in TASK_METHODS:
            try:
                STORE.record(task_id, method, False, {"error": str(error)})
            except Exception:
                pass
        raise

    if task_id and method not in TASK_METHODS:
        details = {}
        if isinstance(result, dict):
            for key in ("run_id", "daemon_reload_run_id"):
                if key in result:
                    details[key] = result[key]
        STORE.record(task_id, method, True, details)
    return result


WORKFLOWS = WorkflowManager(STORE, dispatch)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request_id = None
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise ValueError("request is empty or too large")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")

            result = dispatch(method, params)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve():
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        if not SOCKET_PATH.is_socket():
            raise RuntimeError(f"refusing to replace non-socket: {SOCKET_PATH}")
        SOCKET_PATH.unlink()
    old_umask = os.umask(0o177)
    try:
        with Server(str(SOCKET_PATH), Handler) as server:
            os.umask(old_umask)
            server.serve_forever()
    finally:
        os.umask(old_umask)
        SOCKET_PATH.unlink(missing_ok=True)
