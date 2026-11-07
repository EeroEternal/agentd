import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

SNAPSHOTS = Path("/var/lib/agentos/snapshots")
BOOT_BACKUPS = Path("/var/lib/agentos/boot-backups")
LIMINE = Path("/boot/limine.conf")
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
ENTRY = re.compile(r"[A-Za-z0-9 -]+/linux")


def validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid recovery name")
    return name


def create_snapshot(name):
    validate_name(name)
    target = SNAPSHOTS / name
    if target.exists():
        raise ValueError(f"snapshot already exists: {name}")
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/btrfs", "subvolume", "snapshot", "/", str(target)], check=True, capture_output=True, text=True, timeout=120)
    return {"name": name, "path": str(target), "read_only": False}


def list_snapshots():
    if not SNAPSHOTS.exists():
        return {"snapshots": []}
    return {"snapshots": sorted(path.name for path in SNAPSHOTS.iterdir() if path.is_dir())}


def delete_snapshot(name):
    target = SNAPSHOTS / validate_name(name)
    if not target.is_dir():
        raise ValueError("snapshot does not exist")
    if current_default() == f"AgentOS Recovery {name}/linux":
        raise ValueError("cannot delete the snapshot selected for the next boot")
    subprocess.run(["/usr/bin/btrfs", "subvolume", "delete", str(target)], check=True, capture_output=True, text=True, timeout=120)
    entry = f"/+AgentOS Recovery {name}"
    lines = LIMINE.read_text().splitlines()
    kept = []
    removing = False
    for line in lines:
        if line.startswith("/+"):
            removing = line.strip() == entry
        if not removing:
            kept.append(line)
    LIMINE.write_text("\n".join(kept) + "\n")
    return {"name": name, "deleted": True}


def current_default():
    for line in LIMINE.read_text().splitlines():
        if line.startswith("default_entry:"):
            return line.split(":", 1)[1].strip()
    return None


def set_default(entry):
    if not isinstance(entry, str) or not ENTRY.fullmatch(entry):
        raise ValueError("invalid boot entry")
    text = LIMINE.read_text()
    if not any(line.strip().startswith("/+") and line.strip()[2:] == entry.rsplit("/", 1)[0] for line in text.splitlines()):
        raise ValueError(f"boot entry does not exist: {entry}")
    BOOT_BACKUPS.mkdir(parents=True, exist_ok=True)
    backup_id = uuid.uuid4().hex
    backup = BOOT_BACKUPS / f"{backup_id}.conf"
    backup.write_text(text)
    lines = []
    changed = False
    for line in text.splitlines():
        if line.startswith("default_entry:"):
            lines.append(f"default_entry: {entry}")
            changed = True
        else:
            lines.append(line)
    if not changed:
        raise ValueError("limine default_entry is missing")
    LIMINE.write_text("\n".join(lines) + "\n")
    return {"default_entry": entry, "backup_id": backup_id}


def restore_boot(backup_id):
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{32}", backup_id):
        raise ValueError("invalid boot backup id")
    backup = BOOT_BACKUPS / f"{backup_id}.conf"
    if not backup.is_file():
        raise ValueError("boot backup does not exist")
    LIMINE.write_text(backup.read_text())
    return {"restored": True, "default_entry": current_default()}


def restore_snapshot(name):
    validate_name(name)
    target = SNAPSHOTS / name
    if not target.is_dir():
        raise ValueError("snapshot does not exist")
    text = LIMINE.read_text()
    vm = text.split("/+AgentOS KVM", 1)[1].split("/+", 1)[0]
    kernel = re.search(r"^\s+path: (.+)$", vm, re.M).group(1)
    initrd = re.search(r"^\s+module_path: (.+)$", vm, re.M).group(1)
    entry_name = f"AgentOS Recovery {name}"
    entry = (
        f"\n/+{entry_name}\n"
        "  //linux\n"
        "  comment: Boot AgentOS from a Btrfs recovery snapshot\n"
        "  protocol: linux\n"
        f"  path: {kernel}\n"
        f"  module_path: {initrd}\n"
        "  cmdline: cryptdevice=PARTUUID=e553df7c-02:root root=/dev/mapper/root zswap.enabled=0 "
        f"rootflags=subvol=/@/var/lib/agentos/snapshots/{name} rw rootfstype=btrfs loglevel=7 "
        "cryptkey=rootfs:/etc/agentos/luks.key init=/usr/local/lib/agentd/native-init.py\n"
    )
    if f"/+{entry_name}" not in text:
        LIMINE.write_text(text + entry)
    return set_default(f"{entry_name}/linux")
