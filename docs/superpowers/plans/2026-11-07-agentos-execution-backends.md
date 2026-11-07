# AgentOS Execution Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real AgentCell isolation and an explicitly availability-gated KVM execution backend to AgentOS.

**Architecture:** Python wrappers construct validated `sand` and QEMU argv arrays and reuse the existing logged executor. The planner distinguishes ordinary isolated execution from privileged host and independent-kernel operations.

**Tech Stack:** Python standard library, AgentCell `sand`, Linux namespaces/Landlock/seccomp/cgroups, QEMU/KVM.

---

### Task 1: AgentCell command backend

**Files:** Create `agentd/cell.py` and `agentd/cell_launcher.py`; modify `agentd/native-init.py`, `agentd/server.py`, `agentd/planner.py`, and `agentd/workflows.py`.

- [ ] Write inline tests asserting invalid argv/timeouts are rejected and a valid command uses the drop-privilege launcher with a delegated `run.service/launcher` cgroup before invoking `sand --net none`.
- [ ] Confirm the tests fail because `agentd.cell` does not exist.
- [ ] Implement `status()` and `execute(argv, timeout)` with fixed no-network policy, resource limits, and backend metadata.
- [ ] Add `cell.exec`/`cell.status` dispatch, an OpenAI `cell_exec` tool, and prompt rules preferring it over host `exec` for ordinary commands.
- [ ] Run inline tests and `python3 -m py_compile` before deployment.

### Task 2: KVM command backend

**Files:** Create `agentd/vm.py`; modify `agentd/server.py`, `agentd/planner.py`, and `agentd/workflows.py`.

- [ ] Write inline tests for unavailable KVM and QEMU command construction, including `-enable-kvm`, `-nographic`, virtio disk, memory, CPU, and timeout arguments.
- [ ] Implement `status()` and `run(params)` without a TCG fallback. Validate all paths and numeric limits before invoking the executor.
- [ ] Add `vm.run`/`vm.status` dispatch and planner tools `vm_run`/`vm_status`.
- [ ] Verify clear failure on the current ThinkPad and record that real guest validation requires BIOS VT-x.

### Task 3: Build, deploy, and integrate

- [ ] Clone AgentCell on the ThinkPad, run `make check`, build its minimal rootfs, install `sand`, create the `agentcell` account, and set workspace ownership.
- [ ] Install QEMU and probe `kvm_intel`; preserve the resulting hardware availability evidence.
- [ ] Deploy AgentOS modules and restart `agentd` plus `agentos-gateway`.
- [ ] Verify direct `cell.exec`, actual isolation, and natural-language `a do` tasks. Verify `vm.status` honestly reports the hardware blocker.

### Task 4: Documentation and delivery

- [ ] Update README architecture, tools, security boundaries, and KVM prerequisite.
- [ ] Commit and push AgentOS changes to `EeroEternal/agentd` only after all available checks pass.
- [ ] Leave AgentCell unchanged unless a reproduced upstream defect requires a fix; report any KVM validation that remains blocked.
