# AgentOS Execution Backends Design

## Goal

Make AgentCell the default isolated command executor for AgentOS, preserve explicit privileged host operations, and add a KVM backend for tasks that require an independent kernel.

## Architecture

The planner exposes three distinct execution paths:

- `cell_exec`: ordinary shell commands and untrusted code. A small privileged launcher creates a delegated `run.service/launcher` cgroup, joins it, and drops to the dedicated `agentcell` user before invoking `sand`. AgentCell creates its payload cgroup beside the launcher leaf, avoiding the cgroup v2 no-internal-process constraint. Cells have no network, a curated root filesystem, a dedicated workspace, enforced CPU/memory/PID limits, and a timeout.
- `exec`: privileged host operations. It remains available for system administration but is discouraged for ordinary commands.
- `vm_run`: high-risk guest-kernel tasks. It constructs a bounded QEMU/KVM invocation with an explicit kernel, optional initrd, raw disk, append line, memory, CPU count, and timeout. Serial output is captured by the existing logged executor.

`cell.status` reports whether the sandbox runtime and rootfs are installed. `vm.status` reports QEMU, `/dev/kvm`, and CPU virtualization availability. VM startup fails explicitly when hardware acceleration is unavailable; it never silently falls back to software emulation.

## Boundaries

- AgentCell runs without the root LSM daemon in this phase, avoiding its current shared control socket and fail-open policy. Network is fixed to `none`; host and veth networking are not exposed through the planner.
- The AgentOS planner, browser, MCP servers, and host resource APIs remain outside cells.
- Cell processes run as `agentcell`, not root. The workspace is `/var/lib/agentos/workspace`, shared with the existing filesystem MCP.
- KVM requires `/dev/kvm`. Intel Virtualization Technology is now enabled on the ThinkPad. A raw-disk guest with an independent Arch kernel was booted and powered down through both direct RPC and the natural-language planner.

## Validation

- Unit-test cell command construction, argument validation, and result metadata.
- Build AgentCell and run its upstream test suite on the ThinkPad.
- Run real cell commands, verify PID/network/filesystem isolation and workspace persistence.
- Unit-test KVM availability and QEMU argument construction; verify unavailable hardware returns an actionable error.
- Run natural-language tasks proving ordinary execution selects `cell_exec`, while host administration remains available.

## Deployment

Install `sand` under `/usr/local/bin`, the AgentCell rootfs under `/var/lib/agentos/cell-root`, and the unprivileged account as `agentcell`. Install QEMU for the VM backend. Do not modify the AgentCell upstream source unless integration exposes a defect.
