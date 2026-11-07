import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentd import cell, recovery, tasks, vm_manager
from agentd.planner import run_goal

TOKEN = Path("/etc/agentd/gateway.token").read_text(encoding="utf-8").strip()
MAX_BODY = 1_048_576


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, result):
        response = json.dumps(result, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_POST(self):
        if self.path not in {"/cli", "/do", "/vm", "/cell", "/tasks", "/files", "/download", "/undo", "/recovery"} or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            self.send_error(400)
            return
        task_id = None
        try:
            request = json.loads(self.rfile.read(length))
            if self.path == "/do":
                task_id = tasks.start(request.get("goal"))
                try:
                    result = run_goal(request.get("goal"), request.get("capabilities"))
                    tasks.finish(task_id, result)
                except Exception as error:
                    tasks.fail(task_id, error)
                    raise
            elif self.path == "/vm":
                result = self.vm_action(request)
            elif self.path == "/cell":
                if request.get("action") == "status":
                    result = cell.status()
                else:
                    result = cell.execute(request.get("argv"), request.get("timeout", 60))
            elif self.path == "/tasks":
                result = tasks.last()
            elif self.path == "/files":
                result = tasks.list_files()
            elif self.path == "/download":
                data = tasks.download(request.get("path"))
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            elif self.path == "/undo":
                result = tasks.undo_last()
            elif self.path == "/recovery":
                result = self.recovery_action(request)
            else:
                result = self.cli_action(request)
            self.send_json(200, result)
        except Exception as error:
            self.send_json(500, {"status": "failed", "error": str(error)})

    def recovery_action(self, request):
        action = request.get("action")
        if action == "create":
            return recovery.create_snapshot(request.get("name"))
        if action == "list":
            return recovery.list_snapshots()
        if action == "delete":
            return recovery.delete_snapshot(request.get("name"))
        if action == "restore":
            return recovery.restore_snapshot(request.get("name"))
        if action == "set_default":
            return recovery.set_default(request.get("entry"))
        if action == "restore_boot":
            return recovery.restore_boot(request.get("backup_id"))
        if action == "default":
            return {"default_entry": recovery.current_default()}
        raise ValueError("unknown recovery action")

    def vm_action(self, request):
        action = request.get("action")
        if action == "smoke":
            return vm_manager.smoke()
        name = request.get("name")
        if action == "create":
            return vm_manager.create(name, request.get("memory", 512), request.get("cpus", 1))
        if action == "start":
            return vm_manager.start(name)
        if action == "status":
            return vm_manager.status(name)
        if action == "exec":
            return vm_manager.execute(name, request.get("argv"), request.get("timeout", 60))
        if action == "stop":
            return vm_manager.stop(name)
        if action == "snapshot":
            return vm_manager.snapshot(name, request.get("snapshot"))
        if action == "restore":
            return vm_manager.restore(name, request.get("snapshot"))
        if action == "delete":
            return vm_manager.delete(name)
        raise ValueError("unknown VM action")

    def cli_action(self, request):
        args = request.get("args")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError("args must be a string array")
        process = subprocess.run(
            ["sudo", "/usr/local/bin/agentos", *args],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return {
            "stdout": process.stdout,
            "stderr": process.stderr,
            "exit_code": process.returncode,
        }

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("192.168.3.11", 8765), Handler).serve_forever()
