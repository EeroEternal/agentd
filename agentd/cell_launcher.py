import os
import sys
from pathlib import Path

GROUP_PARENT = Path("/sys/fs/cgroup/agentos-cells")


def main(argv):
    if len(argv) < 8 or argv[1] != "--group" or argv[3] != "--uid" or argv[5] != "--gid" or argv[7] != "--":
        raise ValueError("invalid cell launcher arguments")
    group = Path(argv[2])
    if group.parent.parent != GROUP_PARENT or group.name != "launcher" or not group.is_dir():
        raise ValueError("invalid cell cgroup")
    uid = int(argv[4])
    gid = int(argv[6])
    command = argv[8:]
    if not command or not command[0].startswith("/"):
        raise ValueError("cell command must be absolute")
    (group / "cgroup.procs").write_text(str(os.getpid()), encoding="utf-8")
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.execv(command[0], command)


if __name__ == "__main__":
    main(sys.argv)
