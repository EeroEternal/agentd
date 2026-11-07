import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

TASKS = Path("/var/lib/agentos/tasks")
WORKSPACE = Path("/var/lib/agentos/workspace")
MAX_DOWNLOAD = 16_777_216
REVERSIBLE = {"web_fetch", "browser_fetch", "finish", "cell_exec"}
REVERSIBLE_MCP = {
    "read_file", "read_text_file", "read_media_file", "read_multiple_files",
    "write_file", "append_file", "edit_file", "create_directory",
    "list_directory", "list_directory_with_sizes", "directory_tree",
    "move_file", "copy_file", "search_files", "get_file_info",
    "list_allowed_directories",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def task_path(task_id):
    if not isinstance(task_id, str) or not task_id.isalnum() or len(task_id) != 32:
        raise ValueError("invalid task id")
    return TASKS / task_id


def write(task_id, data):
    path = task_path(task_id) / "task.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def start(goal):
    task_id = uuid.uuid4().hex
    directory = task_path(task_id)
    directory.mkdir(parents=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WORKSPACE, directory / "workspace-before", symlinks=True)
    write(task_id, {
        "id": task_id,
        "goal": goal,
        "status": "running",
        "started_at": now(),
        "steps": [],
    })
    return task_id


def finish(task_id, result):
    data = json.loads((task_path(task_id) / "task.json").read_text())
    data.update({
        "status": "succeeded",
        "finished_at": now(),
        "result": result,
        "steps": result.get("steps", []),
    })
    write(task_id, data)
    return data


def fail(task_id, error):
    data = json.loads((task_path(task_id) / "task.json").read_text())
    data.update({"status": "failed", "finished_at": now(), "error": str(error)})
    write(task_id, data)
    return data


def last():
    records = sorted(TASKS.glob("*/task.json"), key=lambda path: path.stat().st_mtime)
    if not records:
        return {"status": "empty"}
    return json.loads(records[-1].read_text())


def list_files():
    files = []
    if WORKSPACE.exists():
        for path in sorted(WORKSPACE.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files.append({
                    "path": str(path.relative_to(WORKSPACE)),
                    "size": path.stat().st_size,
                })
                if len(files) >= 1000:
                    break
    return {"root": str(WORKSPACE), "files": files}


def workspace_file(value):
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    if value.startswith("/home/agent/"):
        value = value[len("/home/agent/"):]
    elif value.startswith(str(WORKSPACE) + "/"):
        value = value[len(str(WORKSPACE) + 1):]
    root = WORKSPACE.resolve()
    candidate = (root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("file must be inside the AgentOS workspace")
    if not candidate.is_file():
        raise ValueError("workspace file does not exist")
    return candidate


def download(value):
    path = workspace_file(value)
    size = path.stat().st_size
    if size > MAX_DOWNLOAD:
        raise ValueError("workspace file exceeds the 16 MiB download limit")
    return path.read_bytes()


def reversible(steps):
    for step in steps:
        method = step.get("method")
        if method in REVERSIBLE:
            continue
        if method.startswith("mcp__filesystem__") and method.rsplit("__", 1)[1] in REVERSIBLE_MCP:
            continue
        return False, method
    return True, None


def undo_last():
    task = last()
    if task.get("status") == "empty":
        raise ValueError("no task to undo")
    if task.get("status") == "undone":
        raise ValueError("the latest task was already undone")
    if task.get("status") != "succeeded":
        raise ValueError("only a succeeded task can be undone")
    allowed, method = reversible(task.get("steps", []))
    if not allowed:
        raise ValueError(f"task contains an operation that cannot be rolled back safely: {method}")
    directory = task_path(task["id"])
    backup = directory / "workspace-before"
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for path in WORKSPACE.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(backup, WORKSPACE, dirs_exist_ok=True, symlinks=True)
    task["status"] = "undone"
    task["undone_at"] = now()
    write(task["id"], task)
    return {"id": task["id"], "status": "undone", "workspace": str(WORKSPACE)}
