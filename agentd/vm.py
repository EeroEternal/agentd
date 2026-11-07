from pathlib import Path

from .executor import execute as logged_execute

QEMU = Path("/usr/bin/qemu-system-x86_64")
KVM = Path("/dev/kvm")
CPUINFO = Path("/proc/cpuinfo")


def cpu_virtualization():
    if not CPUINFO.is_file():
        return False
    return any(
        "vmx" in line.split() or "svm" in line.split()
        for line in CPUINFO.read_text(encoding="utf-8").splitlines()
        if line.startswith("flags")
    )


def status():
    requirements = {
        "qemu": QEMU.is_file(),
        "kvm_device": KVM.exists(),
        "cpu_virtualization": cpu_virtualization(),
    }
    return {
        "backend": "kvm",
        "ready": all(requirements.values()),
        "requirements": requirements,
        "network": "none",
        "snapshot": True,
    }


def existing_file(value, name):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value:
        raise ValueError(f"{name} must be an absolute file path")
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"{name} does not exist: {value}")
    return path


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def run(params):
    if not isinstance(params, dict):
        raise ValueError("vm parameters must be an object")
    state = status()
    if not state["ready"]:
        missing = [name for name, present in state["requirements"].items() if not present]
        raise RuntimeError(
            "KVM is unavailable; enable Intel VT-x/AMD-V in BIOS and load the KVM module; "
            f"missing {', '.join(missing)}"
        )
    kernel = existing_file(params.get("kernel"), "kernel")
    disk = existing_file(params.get("disk"), "disk")
    initrd = params.get("initrd")
    if initrd is not None:
        initrd = existing_file(initrd, "initrd")
    append = params.get("append", "console=ttyS0")
    if not isinstance(append, str) or not append or "\0" in append or len(append) > 4096:
        raise ValueError("append must be a non-empty string of at most 4096 characters")
    memory = integer(params.get("memory", 512), "memory", 128, 4096)
    cpus = integer(params.get("cpus", 1), "cpus", 1, 4)
    timeout = integer(params.get("timeout", 60), "timeout", 1, 3600)
    persist = params.get("persist", False)
    if not isinstance(persist, bool):
        raise ValueError("persist must be a boolean")
    command = [
        str(QEMU),
        "-enable-kvm",
        "-cpu",
        "host",
        "-m",
        str(memory),
        "-smp",
        str(cpus),
        "-kernel",
        str(kernel),
    ]
    if initrd:
        command.extend(["-initrd", str(initrd)])
    command.extend([
        "-drive",
        f"file={disk},format=raw,if=virtio,snapshot={'off' if persist else 'on'}",
        "-append",
        append,
        "-nographic",
        "-monitor",
        "none",
        "-no-reboot",
        "-net",
        "none",
    ])
    result = logged_execute(command, timeout + 5)
    result["backend"] = "kvm"
    result["snapshot"] = not persist
    return result
