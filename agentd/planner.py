import json
import urllib.error
import urllib.request
from pathlib import Path

from .mcp_client import clients
from .__main__ import request as socket_request

CONFIG_PATH = Path("/etc/agentd/llm.json")
MAX_ITERATIONS = 8

LOCAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "native_restart",
            "description": "Restart an AgentOS native service",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "native_status",
            "description": "Get native service PID, state, restarts and exit code",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_health",
            "description": "Check whether AgentOS is ready and get failure reasons",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a UTF-8 text file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Atomically write a UTF-8 text file with backup",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "network_status",
            "description": "Get interfaces, addresses, routes, DNS and NetworkManager state",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Execute a system command as root with an argv array",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                    "timeout": {"type": "integer"},
                },
                "required": ["argv"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a static HTTP(S) page and return its title, plain text, and links",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fetch",
            "description": "Render an HTTP(S) page with headless Chromium and return its title, plain text, and links; use for JavaScript sites",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task and return the final result to the user",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


def config():
    if not CONFIG_PATH.exists():
        raise RuntimeError("LLM provider is not configured: /etc/agentd/llm.json")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def openai_call(settings, messages, tools):
    payload = json.dumps({
        "model": settings["model"],
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    request = urllib.request.Request(
        f"{settings['base_url'].rstrip('/')}/chat/completions",
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM provider HTTP {error.code}: {detail}") from error
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["choices"][0]["message"]


def mcp_tools():
    tools = []
    for name, client in clients().items():
        try:
            client.initialize()
            for tool in client.list_tools():
                tools.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{name}__{tool['name']}",
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                })
        finally:
            client.close()
    return tools


TOOL_METHODS = {
    "native_restart": "native.restart",
    "native_status": "native.status",
    "system_health": "system.health",
    "file_read": "file.read",
    "file_write": "file.write",
    "network_status": "network.status",
    "exec": "exec",
}


def call_agentd(method, params):
    response = socket_request(method, params)
    if not response.get("ok"):
        error = response.get("error", {})
        raise RuntimeError(error.get("message", "agentd request failed"))
    return response["result"]


def execute_tool(name, arguments):
    if name.startswith("mcp__"):
        _, server, tool = name.split("__", 2)
        client = clients()[server]
        try:
            return client.call_tool(tool, arguments)
        finally:
            client.close()
    if name == "web_fetch":
        from .web import fetch
        return fetch(arguments["url"])
    if name == "browser_fetch":
        from .browser import fetch
        return fetch(arguments["url"])
    if name == "finish":
        return {"finished": True, "summary": arguments["summary"]}
    return call_agentd(TOOL_METHODS.get(name, name), arguments)


def mock_goal(goal):
    restart = call_agentd("native.restart", {"name": "agentos-heartbeat-native"})
    status = call_agentd("native.status", {"name": "agentos-heartbeat-native"})
    return {
        "goal": goal,
        "status": "succeeded",
        "result": f"Heartbeat restarted and verified running with PID {status['pid']}",
        "steps": [
            {"method": "native.restart", "result": restart},
            {"method": "native.status", "result": status},
        ],
    }


def run_goal(goal):
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be a non-empty string")
    settings = config()
    if settings.get("mock"):
        return mock_goal(goal)
    settings.setdefault("model", "deepseek-chat")
    tools = [*LOCAL_TOOLS, *mcp_tools()]
    messages = [
        {
            "role": "system",
            "content": (
                "You are the AgentOS planner. Use tools to achieve the user's goal. "
                "For web pages, use web_fetch for static HTML and browser_fetch when JavaScript "
                "rendering is needed or web_fetch returns an empty body. Do not use shell commands "
                "for ordinary web fetching. Inspect state before destructive actions and verify "
                "results. Do not repeat an identical action after "
                "it succeeds. When the goal is complete, call finish with the final answer; for "
                "text extraction requests, put the extracted plain text in the finish summary."
            ),
        },
        {"role": "user", "content": goal},
    ]
    steps = []
    for _ in range(MAX_ITERATIONS):
        message = openai_call(settings, messages, tools)
        messages.append(message)
        calls = message.get("tool_calls", [])
        if not calls:
            return {
                "goal": goal,
                "status": "succeeded",
                "finished": True,
                "result": message.get("content", ""),
                "steps": steps,
            }
        for call in calls:
            name = call["function"]["name"]
            arguments = json.loads(call["function"].get("arguments", "{}"))
            try:
                result = execute_tool(name, arguments)
            except Exception as error:
                result = {"error": str(error)}
            steps.append({"method": name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
            if name == "finish" and result.get("finished"):
                return {
                    "goal": goal,
                    "status": "succeeded",
                    "finished": True,
                    "result": result["summary"],
                    "steps": steps,
                }
    raise RuntimeError("planner iteration limit reached")
