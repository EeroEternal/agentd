import re

from .systemd import run

PACKAGE = re.compile(r"^[a-zA-Z0-9@._+:-]+$")


def names(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError("names must contain 1 to 20 packages")
    if not all(isinstance(name, str) and PACKAGE.fullmatch(name) for name in value):
        raise ValueError("invalid package name")
    return value


def info(params):
    name = names([params.get("name")])[0]
    result = run(["/usr/bin/pacman", "-Qi", name], 30)
    return {"name": name, "installed": True, "info": result["stdout"], "run_id": result["run_id"]}


def list_installed():
    result = run(["/usr/bin/pacman", "-Q"], 30)
    return {"packages": result["stdout"], "run_id": result["run_id"]}


def install(params):
    requested = names(params.get("names"))
    result = run(["/usr/bin/pacman", "-S", "--needed", "--noconfirm", *requested], 3600)
    return {"names": requested, "run_id": result["run_id"]}


def remove(params):
    requested = names(params.get("names"))
    result = run(["/usr/bin/pacman", "-Rns", "--noconfirm", *requested], 3600)
    return {"names": requested, "run_id": result["run_id"]}
