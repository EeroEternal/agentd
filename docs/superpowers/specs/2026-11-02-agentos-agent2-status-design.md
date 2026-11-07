# AgentOS agent2 状态接口设计

## 目标

在已验证可启动的 `7.1.9-agent1` 基线上，生成 `7.1.9-agent2` 内核，加入内核原生的只读 `/proc/agent_status` 接口，并在 Omarchy 实体机上验证该接口及完整启动返回路径。

这是第一个功能性内核修改，用于证明 Agent 不仅能重编译现有内核，还能生成可观察的新内核能力。

## 范围

本阶段只增加一个只读 procfs 接口，不修改调度器、内存管理、权限模型、系统调用表或 PID 1。不引入用户态依赖，不保存秘密或持久化数据。

接口固定输出：

```text
agentos_version=0.2.0
kernel_release=7.1.9-agent2
status=running
```

其中 `kernel_release` 必须从内核自身的 UTS release 获取，不能硬编码伪造。

## 内核实现

在 Arch Linux `v7.1.9-arch1` 源码中新增 `kernel/agentos.c`，并修改 `kernel/Makefile` 将其编译进内核。

实现使用 Linux procfs 和 seq_file 接口：

- 注册名为 `agent_status` 的 proc 根目录条目；
- 权限为 `0444`，所有用户只读；
- 使用 initcall 在内核初始化时创建；
- 输出 AgentOS 功能版本、当前内核 release 和固定运行状态；
- 注册失败时记录内核错误并返回错误码，不主动触发 panic；
- 代码不使用动态模块卸载路径，因为它直接编译进内核。

新增 `CONFIG_LOCALVERSION="-agent2"`，构建时继续覆盖 Arch 源码的 `EXTRAVERSION=`，确保最终 release 是 `7.1.9-agent2`，而不是 `7.1.9-arch1-agent2` 或发行版原始名称。

## 构建与产物

使用当前已验证的 Arch 源码和配置。为缩短迭代时间，可以复用现有构建工作区中的对象，但所有新产物必须安装到独立路径：

- `/srv/agentos/artifacts/agent2/`
- `/usr/lib/modules/7.1.9-agent2/`
- `/boot/agentos/agent2/`

保存本次源码补丁、构建配置、内核镜像、模块和 initramfs。不得覆盖 `/boot/agentos/agent1/` 或原始 Omarchy 启动产物。

initramfs 使用已验证的合并配置，必须继续包含 `encrypt`、`btrfs-overlayfs`、LUKS 解锁和 Omarchy 所需 hooks。不能只使用会产生不完整启动链的主配置。

## Limine 与恢复

新增独立顶层 `AgentOS Test 2` 项，指向 `/boot/agentos/agent2/`。保留：

- 原始 `Omarchy` 项；
- `AgentOS Test`（agent1）项；
- 当前 `default_entry: 2`。

不设置 agent2 为默认启动项，不宣称自动回退。由用户在实体机菜单手动选择，并输入现有 LUKS 密码。

如果构建或 initramfs 失败，不修改 Limine。如果 agent2 无法启动，用户选择原 Omarchy 或 agent1 项。

## 验证

### 构建与安装

- `kernelrelease` 和镜像内嵌版本均为 `7.1.9-agent2`。
- 新 procfs 实现已编译进内核。
- 新模块目录和 initramfs 存在且非空。
- initramfs 包含 `cryptsetup`、`dm-crypt`、`encrypt` 和 `btrfs-overlayfs`。
- 原 Omarchy 和 agent1 启动文件保持不变。

### 实体机

从 `AgentOS Test 2` 启动后：

- `uname -r` 返回 `7.1.9-agent2`；
- `/proc/agent_status` 权限为只读，输出三行规定内容；
- `kernel_release` 与 `uname -r` 完全一致；
- LUKS/Btrfs 根卷正常读写；
- systemd、网络、SSH 和 agentd 正常；
- 存储、显示及网络驱动正常加载。

随后从原 Omarchy 项启动，确认 `uname -r` 恢复为 `7.1.9-arch1-2`，且原系统中不存在我们新增的 `/proc/agent_status`。这同时证明测试接口来自新内核，而不是用户态伪造文件。

只有上述验证全部完成，本阶段才算完成。
