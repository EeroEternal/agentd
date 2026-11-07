# AgentOS Usable Control Plane Design

## Goal

Turn the working AgentOS/AgentCell/KVM proof into a practical control plane with interactive VMs, short commands, reversible file tasks, explicit privileges, and reproducible recovery.

## VM lifecycle

`agentd/vm_manager.py` manages named VMs under `/var/lib/agentos/vms`. A qcow2 overlay protects the raw base image. QEMU runs daemonized with a serial log, monitor socket, and virtio-serial command socket. A Python Guest Agent executes JSON-line requests and returns stdout, stderr, exit status, and errors. The manager supports create, start, status, exec, stop, snapshot, restore, delete, and an end-to-end smoke profile.

## User experience

The Mac `a` client supports direct natural language, `vm`, `cell`, `last`, `files`, `get`, `undo`, `doctor`, and `pair`. Authenticated gateway endpoints expose task history and workspace downloads. Long kernel/disk arguments are replaced by named profiles.

## Transactions and recovery

Each natural-language task records its goal, steps, result, and a pre-task workspace copy. Undo restores workspace changes only when every executed step belongs to the reversible set; privileged host mutations fail explicitly rather than claiming rollback. Btrfs snapshot helpers and boot-default backup/recovery cover system-level recovery.

## Privileges

The planner receives per-request capabilities. Ordinary goals allow cells, browser, and workspace operations. Host exec, arbitrary file writes, and direct VM boot require explicit user authorization. Named VM lifecycle operations are always explicit user actions. AgentCell secure mode must fail closed, and its root control socket is restricted to the trusted agentcell group.

## Operations

`agentos doctor` validates startup permissions, cgroups, rootfs, Python/ctypes, network, browser, MCP, and KVM without printing secrets. Install/update/recover commands are idempotent and preserve backups. Regression tests cover Python behavior and real native boot, cell, and KVM smoke checks.

## Verification

Every feature receives focused unit checks and real ThinkPad verification. VM verification includes a guest-side command with changing state, snapshot restore proving that state reverted, and clean shutdown. Undo verification modifies and then restores a workspace file. Security tests prove unauthorized host actions fail. No feature is reported complete from prose alone.
