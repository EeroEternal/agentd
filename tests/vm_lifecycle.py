import json

from agentd import vm_manager

NAME = "agentos-smoke"


def show(step, value):
    print(json.dumps({"step": step, "result": value}, ensure_ascii=False), flush=True)


def main():
    if vm_manager.status(NAME)["exists"]:
        vm_manager.delete(NAME)
    try:
        show("create", vm_manager.create(NAME))
        show("start", vm_manager.start(NAME))
        show("guest_status", vm_manager.request(NAME, "status"))
        result = vm_manager.execute(NAME, ["uname", "-r"])
        assert result["exit_code"] == 0, result
        show("exec_uname", result)
        vm_manager.stop(NAME)
        show("snapshot_clean", vm_manager.snapshot(NAME, "clean"))
        vm_manager.start(NAME)
        result = vm_manager.execute(NAME, ["python3", "-c", "open('/vm-proof.txt', 'w').write('dirty')"])
        assert result["exit_code"] == 0, result
        vm_manager.stop(NAME)
        show("restore_clean", vm_manager.restore(NAME, "clean"))
        vm_manager.start(NAME)
        result = vm_manager.execute(NAME, ["python3", "-c", "import os; print(os.path.exists('/vm-proof.txt'))"])
        assert result["stdout"].strip() == "False", result
        show("snapshot_reverted", result)
        vm_manager.stop(NAME)
        show("stopped", vm_manager.status(NAME))
    finally:
        if vm_manager.status(NAME)["exists"]:
            show("delete", vm_manager.delete(NAME))


if __name__ == "__main__":
    main()
