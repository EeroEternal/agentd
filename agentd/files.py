import hashlib
import os
import stat
import uuid
from pathlib import Path

MAX_BYTES = 1_048_576
BACKUPS = Path("/var/lib/agentd/backups")


def absolute(value):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value:
        raise ValueError("path must be an absolute string")
    return Path(value)


def read(params):
    path = absolute(params.get("path"))
    if not path.is_file():
        raise FileNotFoundError(path)
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("file exceeds the 1 MiB limit")
    return {
        "path": str(path),
        "content": data.decode("utf-8"),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def backup(path):
    if not path.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = BACKUPS / f"{uuid.uuid4().hex}-{path.name}"
    target.write_bytes(path.read_bytes())
    return str(target)


def write(params):
    path = absolute(params.get("path"))
    content = params.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("content must be text of at most 1 MiB")
    data = content.encode("utf-8")
    if params.get("parents", True):
        path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup(path) if params.get("backup", True) else None
    mode = params.get("mode")
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
        raise ValueError("invalid file mode")
    temporary = path.with_name(f".{path.name}.agentd.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return {
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "backup": backup_path,
    }


def restore(params):
    path = absolute(params.get("path"))
    requested = params.get("backup")
    if requested is not None:
        candidate = absolute(requested)
        if candidate.resolve().parent != BACKUPS.resolve():
            raise ValueError("backup must be inside the agentd backup directory")
    else:
        candidates = sorted(
            BACKUPS.glob(f"*-{path.name}"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(f"no backups for {path}")
        candidate = candidates[0]
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    data = candidate.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("backup exceeds the 1 MiB limit")
    backup_path = backup(path) if path.exists() else None
    temporary = path.with_name(f".{path.name}.agentd.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return {
        "path": str(path),
        "restored_from": str(candidate),
        "backup": backup_path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def list_directory(params):
    path = absolute(params.get("path"))
    if not path.is_dir():
        raise NotADirectoryError(path)
    entries = []
    for entry in path.iterdir():
        info = entry.lstat()
        entries.append({
            "name": entry.name,
            "type": "directory" if entry.is_dir() else "file" if entry.is_file() else "other",
            "size": info.st_size,
            "mode": stat.S_IMODE(info.st_mode),
        })
        if len(entries) >= 1000:
            break
    return {"path": str(path), "entries": entries}


def delete(params):
    path = absolute(params.get("path"))
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(path)
    if path.is_dir() and not path.is_symlink():
        raise IsADirectoryError(path)
    backup_path = backup(path) if params.get("backup", True) else None
    path.unlink()
    return {"path": str(path), "deleted": True, "backup": backup_path}
