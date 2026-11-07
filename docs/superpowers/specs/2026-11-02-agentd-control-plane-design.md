# AgentD 系统控制面设计

## 目标

在当前 AgentOS `7.1.9-agent2` 实体机上，将 `agentd` 从单一 root 命令执行器升级为具有结构化系统管理接口、持久化任务状态和可验证故障处理能力的系统控制守护进程。

本阶段通过一个真实 systemd 服务验证 Agent 的感知、计划、执行、验证和失败处理闭环，不声称已经完成最终 Agent OS。

## 范围

保留现有 `exec` 方法及其完整 root 能力。新增系统状态、AgentOS 服务管理和任务记录接口，不引入第三方 Python 依赖，不新增网络监听，不修改内核或启动项。

首个演示服务为 `agentos-heartbeat.service`。它定期写入可观察日志，并由 systemd 在异常退出后自动重启。

## 组件

### 任务存储

新增独立任务存储组件，根目录为 `/var/lib/agentd/tasks/`。

每个任务包含：

- 唯一任务 ID；
- 用户或 Agent 提供的高层目标；
- `created`、`running`、`succeeded` 或 `failed` 状态；
- 创建、更新和完成时间；
- 有序事件列表。

每个事件记录时间、动作、是否成功以及结构化详情。运行命令时关联现有 `/srv/agentos/runs/` 中的日志 ID，而不是复制完整命令输出到任务文件。

任务文件使用同目录临时文件和原子替换写入，服务重启后可重新读取。任务存储需要进程内锁，防止并发请求破坏事件顺序或文件内容。

### 系统状态

新增 `system.status`，至少返回：

- 内核 release；
- 当前主机名；
- systemd 运行状态；
- `agentd` 和 `sshd` 的 active 状态；
- 当前时间。

状态读取失败必须返回明确错误，不把部分结果伪装成完整健康状态。

### AgentOS 服务管理

新增：

- `service.install`
- `service.start`
- `service.stop`
- `service.restart`
- `service.status`
- `service.logs`

`service.install` 接收服务名、执行命令、重启策略和重启间隔，原子写入 `/etc/systemd/system/<name>`，然后执行 `systemctl daemon-reload`。

为避免误覆盖系统服务，结构化服务接口只接受以 `agentos-` 开头且以 `.service` 结尾的合法单元名。现有 root `exec` 不受这一命名约束。

`service.status` 使用 `systemctl show` 返回结构化属性，至少包括 `ActiveState`、`SubState`、`MainPID`、`NRestarts` 和 `ExecMainStatus`。不存在的服务必须返回明确的非成功结果。

`service.logs` 使用 `journalctl` 返回指定数量的最新日志，默认 20 行。

所有 systemd 命令通过现有执行器运行，从而复用超时、进程组终止和完整日志落盘。非零退出必须作为操作失败处理。

### 服务与任务关联

每个服务接口接受可选 `task_id`。提供时，任务存储追加对应事件；任务不存在时返回错误，不隐式创建任务。

`task.complete` 只允许将未完成目标任务为 `succeeded`；`task.fail` 记录失败原因并将状态置为 `failed`。已经完成的任务不能再次改变状态。

## JSON 接口

沿用 root-only Unix Socket `/run/agentd/agentd.sock` 和 JSON-lines 请求格式。

请求：

```json
{"id":"request-id","method":"service.status","params":{"name":"agentos-heartbeat.service","task_id":"task-id"}}
```

成功响应：

```json
{"id":"request-id","ok":true,"result":{}}
```

失败响应：

```json
{"id":"request-id","ok":false,"error":{"type":"ErrorType","message":"specific failure"}}
```

CLI 增加通用 `call METHOD JSON` 形式，便于 Agent 调用所有方法；保留现有 `exec --timeout` 用法。

## 错误处理

- 参数、服务名或任务 ID 非法时，在执行副作用前拒绝请求。
- systemd 命令失败时返回退出状态和有界错误摘要，完整输出仍在运行日志中。
- 服务安装写文件失败时保留原单元文件，不执行 daemon-reload。
- 任务存储损坏或不可读时返回错误，不覆盖原文件。
- 单个请求失败不终止 `agentd`。
- systemd 服务崩溃由 systemd 按单元配置重启，Agent 通过状态接口确认恢复。

## 实体机闭环演示

Agent 从高层目标“创建并维护 agentos-heartbeat 服务，验证故障恢复”开始，自行执行：

1. 创建任务并读取系统状态。
2. 安装带 `Restart=on-failure` 的 heartbeat 服务。
3. 启动服务，验证 active 状态和日志。
4. 读取 MainPID 与 NRestarts。
5. 通过现有 root `exec` 对该 PID 发送 SIGKILL，模拟进程异常。
6. 轮询服务状态，直到出现新的非零 MainPID、active 状态且 NRestarts 增加；超时则明确失败。
7. 将任务标记为 succeeded，读取任务记录验证事件完整。
8. 最后确认 systemd、SSH 和 agentd 持续正常。

演示结束后保留 heartbeat 服务运行，不重启实体机，不返回原内核。

## 完成标准

- 所有结构化接口和任务接口可用，现有 exec 用法保持兼容。
- 任务在 agentd 重启后仍可读取，事件和状态一致。
- heartbeat 服务能启动、写日志并在 SIGKILL 后自动重启。
- Agent 完成高层任务并生成完整任务记录。
- agent2、Btrfs 根卷、systemd、SSH 和 agentd 在演示后保持正常。
