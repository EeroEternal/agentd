# AgentOS Usable Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver interactive VM execution, short user commands, reversible workspace tasks, explicit privileges, and reproducible operations.

**Architecture:** Add focused Python modules for VM lifecycle, task history, workspace transactions, and policy. Extend the authenticated gateway and Mac client. Harden AgentCell secure startup and add operational CLI/tests.

**Tech Stack:** Python stdlib, QEMU/KVM, virtio-serial, qcow2, Btrfs, AgentCell, unittest.

---

### Task 1: Interactive VM lifecycle

- [ ] Test VM naming, qcow2 paths, QEMU arguments, monitor/virtio-serial protocol, and invalid actions.
- [ ] Implement `vm_manager.py` and a persistent Python Guest Agent.
- [ ] Build a raw base image, create a qcow2 VM, execute commands, snapshot, restore, stop, and delete it.

### Task 2: Short commands and artifacts

- [ ] Add gateway action endpoints for history, workspace listing, and authenticated downloads.
- [ ] Add `a <goal>`, `a vm`, `a cell`, `a last`, `a files`, and `a get` while retaining `a do`.

### Task 3: Transactions and recovery

- [ ] Record natural-language task steps and pre-task workspace state.
- [ ] Restore changed workspace files for reversible tasks and reject unsupported rollback.
- [ ] Add Btrfs snapshot and boot-default backup/recovery helpers.

### Task 4: Privileges and AgentCell hardening

- [ ] Enforce per-request host privileges in the planner and tool dispatch.
- [ ] Make AgentCell `--secure` fail closed and restrict its control socket to the trusted group.
- [ ] Run upstream and real unauthorized-action tests.

### Task 5: Operations and regression tests

- [ ] Implement idempotent install, doctor, pair, update, and recover commands.
- [ ] Add Python regression tests and real native/cell/KVM smoke checks.
- [ ] Update README, verify all examples, and commit both repositories.
