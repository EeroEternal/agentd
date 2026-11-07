import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentd.planner import run_goal

TOKEN = Path("/etc/agentd/gateway.token").read_text(encoding="utf-8").strip()
MAX_BODY = 1_048_576


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in {"/cli", "/do"} or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            self.send_error(400)
            return
        request = json.loads(self.rfile.read(length))
        if self.path == "/do":
            try:
                result = run_goal(request.get("goal"))
                status = 200
            except Exception as error:
                result = {"status": "failed", "error": str(error)}
                status = 500
            response = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        args = request.get("args")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            self.send_error(400)
            return
        process = subprocess.run(
            ["sudo", "/usr/local/bin/agentos", *args],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        response = json.dumps({
            "stdout": process.stdout,
            "stderr": process.stderr,
            "exit_code": process.returncode,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("192.168.3.11", 8765), Handler).serve_forever()
