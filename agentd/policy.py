from pathlib import Path

WORKSPACE = Path("/var/lib/agentos/workspace")
HOST_TOOLS = {"exec", "native_restart"}


def capabilities(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("capabilities must be an object")
    return {
        "host": value.get("host", False) is True,
        "vm": value.get("vm", False) is True,
    }


def authorize(name, arguments, requested=None):
    allowed = capabilities(requested)
    if name in HOST_TOOLS and not allowed["host"]:
        raise PermissionError(f"{name} requires explicit host capability")
    if name == "vm_run" and not allowed["vm"]:
        raise PermissionError("vm_run requires explicit VM capability")
    if name == "file_write" and not allowed["host"]:
        value = arguments.get("path") if isinstance(arguments, dict) else None
        if not isinstance(value, str) or not value.startswith("/"):
            raise PermissionError("file_write outside the workspace requires host capability")
        root = WORKSPACE.resolve()
        if value.startswith("/home/agent/"):
            value = str(root / value[len("/home/agent/"):])
        candidate = Path(value).resolve()
        if candidate != root and root not in candidate.parents:
            raise PermissionError("file_write outside the workspace requires host capability")
