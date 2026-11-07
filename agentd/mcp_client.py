import json
import os
import select
import subprocess
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("/etc/agentd/mcp.json")
TIMEOUT = 30


class MCPClient:
    def __init__(self, name, url=None, headers=None, command=None, args=None, env=None):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.counter = 0
        self.process = None
        self.initialized = False

    def rpc(self, method, params=None):
        self.counter += 1
        request_id = self.counter
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        if self.command:
            result = self.stdio_rpc(request_id, payload)
        else:
            request = urllib.request.Request(
                self.url,
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    **self.headers,
                },
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result = json.load(response)
        if "error" in result:
            raise RuntimeError(f"MCP {self.name} error: {result['error']}")
        return result["result"]

    def start(self):
        if self.process is not None:
            return
        try:
            self.process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={**os.environ, **self.env},
            )
        except OSError as error:
            raise RuntimeError(f"cannot start MCP {self.name}: {error}") from error

    def stdio_rpc(self, request_id, payload):
        self.start()
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        while True:
            ready, _, _ = select.select([self.process.stdout], [], [], TIMEOUT)
            if not ready:
                raise TimeoutError(f"MCP {self.name} did not respond within {TIMEOUT} seconds")
            line = self.process.stdout.readline()
            if not line:
                detail = ""
                if self.process.poll() is not None:
                    detail = self.process.stderr.read()[-2000:]
                raise RuntimeError(f"MCP {self.name} closed its output: {detail}")
            response = json.loads(line)
            if response.get("id") == request_id:
                return response

    def initialize(self):
        if self.initialized:
            return None
        result = self.rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "agentos", "version": "0.1.0"},
        })
        if self.command:
            self.process.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }) + "\n")
            self.process.stdin.flush()
        self.initialized = True
        return result

    def list_tools(self):
        self.initialize()
        return self.rpc("tools/list", {})["tools"]

    def call_tool(self, tool, arguments):
        self.initialize()
        return self.rpc("tools/call", {"name": tool, "arguments": arguments})

    def close(self):
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.stdin:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def clients():
    if not CONFIG_PATH.exists():
        return {}
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    result = {}
    for name, server in config.get("servers", {}).items():
        if server.get("command"):
            result[name] = MCPClient(
                name,
                command=server["command"],
                args=server.get("args"),
                env=server.get("env"),
            )
        else:
            result[name] = MCPClient(name, server["url"], server.get("headers"))
    return result
