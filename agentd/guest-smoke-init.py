#!/usr/bin/python3
import ctypes
import json
import os
import platform
import time

libc = ctypes.CDLL(None, use_errno=True)
for filesystem, target in (("proc", "/proc"), ("sysfs", "/sys"), ("devtmpfs", "/dev")):
    os.makedirs(target, exist_ok=True)
    if libc.mount(filesystem.encode(), target.encode(), filesystem.encode(), 0, None) != 0:
        error = ctypes.get_errno()
        if error != 16:
            raise OSError(error, os.strerror(error), target)

print("AGENTOS_GUEST_BOOT_OK", flush=True)
print(json.dumps({
    "kernel": platform.release(),
    "machine": platform.machine(),
    "root": os.statvfs("/").f_bsize,
    "pid": os.getpid(),
}), flush=True)
os.sync()
time.sleep(1)
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall(169, 0xFEE1DEAD, 672274793, 0x4321FEDC, 0)
while True:
    time.sleep(3600)
