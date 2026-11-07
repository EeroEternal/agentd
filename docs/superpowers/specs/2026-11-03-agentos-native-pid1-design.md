# AgentOS Native PID 1 原型设计

## 目标

在当前实体机上新增独立 `AgentOS Native` 启动路径：使用已验证的 `7.1.9-agent2` 内核和完整 LUKS initramfs，但用 `agentd` 原生初始化程序替代 systemd 作为 PID 1。

原型必须提供原生进程监督、网络、SSH、任务和工作流能力，同时保留所有已有启动项。

## 范围

这是最小 Agent-native OS 原型，不是生产级通用操作系统。本阶段不修改内核源码，不删除 systemd 文件，不改变默认启动项，不自动覆盖现有用户态。

## 原生初始化

新增 `native-init.py`，由 Limine 通过以下参数启动：

```text
init=/usr/local/lib/agentd/native-init.py
```

程序使用 shebang 调用 `/usr/bin/python`，在 PID 1 中：

1. 设置基本环境和主机名；
2. 确认或挂载 proc、sysfs、devtmpfs、`/run` tmpfs 和 `/boot`；
3. 挂载 Omarchy 的 `@home`、`@log`、`@pkg` Btrfs 子卷；
4. 创建运行目录并修正 resolved 目录属主；
5. 创建 `/run/agentos/native` 标记；
6. 启动原生进程监督器；
7. 启动 `agentd` Unix Socket 服务线程；
8. 按优先级启动持久化定义的原生服务；
9. 执行 `udevadm trigger --action=add` 和 `udevadm settle`，确保已有网卡被枚举；
10. 保持主线程存活并处理终止信号。

初始化失败时向控制台输出明确错误，不进入静默重启循环。自动诊断服务会持久化接口、进程和服务状态；问题控制台可以禁用，不要求本地键盘输入。

## 原生进程监督器

新增独立 `native.py`，提供：

- `native.install`
- `native.start`
- `native.stop`
- `native.restart`
- `native.status`
- `native.logs`
- `native.list`

服务定义保存在 `/etc/agentd/services/<name>.json`，包括：

- 合法名称；
- argv 数组；
- 重启策略和重启间隔；
- 启动优先级；
- 是否随原生启动自动启动。

每个服务由独立监控线程等待进程。异常退出时按策略重启，记录 PID、重启次数、退出码和日志路径。停止时先终止进程组，超时后强制杀死。

服务日志保存在 `/var/log/agentd/<name>.log`。结构化接口只允许管理 `agentos-` 前缀服务；原生命令仍由 Agent 明确提交。

## 基础服务

在实体机现有用户态中安装以下原生定义：

- udev：`/usr/lib/systemd/systemd-udevd`
- D-Bus：`/usr/bin/dbus-daemon --system --nofork`
- NetworkManager：`/usr/bin/NetworkManager --no-daemon`
- systemd-resolved：`/usr/lib/systemd/systemd-resolved`
- SSH：`/usr/bin/sshd -D`
- 本地调试控制台：`/usr/bin/agetty --autologin root --noclear tty1`

这些程序仅作为可复用用户态组件运行，不由 systemd 管理。启动顺序由优先级控制。监督器在控制台打印定义数量、每个服务的 PID 和启动错误；本地控制台保证没有网络时仍能诊断。Native 服务接口与现有 systemd 服务接口分离，避免在原生模式下调用不存在的 systemctl。

## 系统状态与工作流

`system.status` 在原生模式下不再调用 systemctl，而是返回：

- 内核和主机名；
- `init=agentd-native`；
- `agentd=active`；
- 原生 SSH 服务状态；
- 当前时间。

现有任务与工作流存储继续使用 `/var/lib/agentd/`。工作流引擎逐步加入 `native.*` 方法到允许列表，使 Agent 可以提交原生服务操作和结果断言。

## 启动项与恢复

新增独立顶层 `AgentOS Native` Limine 项：

- 使用 `/boot/agentos/agent2/vmlinuz`；
- 使用 `/boot/agentos/agent2/initramfs`；
- 沿用 LUKS、Btrfs 和根卷参数；
- 仅新增 `init=/usr/local/lib/agentd/native-init.py` 及诊断输出参数。

原 Omarchy、agent1、agent2 和默认启动项保持不变。若原生系统无法启动或网络不可用，用户从已有项恢复。

## 验证

### 安装阶段

- 所有 Python 源码语法检查通过；
- 原生服务定义合法；
- 新启动项和已有启动项同时存在；
- 默认启动项不变。

### 原生启动

从 `AgentOS Native` 启动后：

- PID 1 是原生初始化程序；
- 根卷、proc、sys、dev、run 可用；
- `agentd` Socket 可调用；
- 原生 SSH 可远程登录；
- NetworkManager 恢复现有网络连接；
- 原生服务状态、日志、任务和工作流接口可用。

### 实际闭环

Agent 通过原生接口安装并启动 heartbeat，杀死进程并验证监督器自动重启，再提交包含结果断言的工作流。所有记录持久化且可读取。

只有这些实体机验证完成，最小原生原型才算完成。
