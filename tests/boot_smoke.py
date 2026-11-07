import json

from agentd import cell, recovery, vm_manager

result = {
    "default_entry": recovery.current_default(),
    "cell": cell.execute(["python3", "-c", "import ctypes; print('cell-boot-ok')"], 15),
    "vm": vm_manager.smoke(),
}
assert result["default_entry"] == "AgentOS KVM/linux", result["default_entry"]
assert result["cell"]["exit_code"] == 0, result["cell"]
assert "cell-boot-ok" in result["cell"]["stdout"], result["cell"]
assert result["vm"]["marker"] == "AGENTOS_VM_SMOKE_OK", result["vm"]
print(json.dumps(result, ensure_ascii=False, indent=2))
