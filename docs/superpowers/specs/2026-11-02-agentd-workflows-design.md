# AgentD 自主工作流设计

## 目标

在现有结构化系统接口和任务存储之上，增加持久化、可后台执行的 Agent 工作流。Agent 提交目标和有序步骤后，`agentd` 自主执行、验证、记录成功或失败，不再要求 Agent 逐条同步调用。

本阶段不引入本地大模型推理，不修改内核，不重启实体机。

## 工作流模型

新增 `workflow.start`、`workflow.get` 和 `workflow.list`。

`workflow.start` 接收：

- `goal`：高层目标；
- `steps`：非空步骤数组。

每个步骤包含：

- `name`：工作流内唯一名称；
- `method`：现有系统管理方法；
- `params`：方法参数对象；
- 可选 `expect`：必须存在于结果中的嵌套字段和值；
- 可选 `attempts`：最大执行次数，默认 1，范围 1–20；
- 可选 `delay`：重试间隔秒数，范围 0–10。

首批允许工作流调用的方法为 `exec`、`system.status`、`service.install`、`service.start`、`service.stop`、`service.restart`、`service.status` 和 `service.logs`。禁止工作流步骤递归调用工作流或直接修改任务终态。

## 执行与持久化

工作流存储在 `/var/lib/agentd/workflows/`，每个工作流包含：

- 唯一 ID 和关联任务 ID；
- 目标、原始步骤和当前状态；
- `queued`、`running`、`succeeded` 或 `failed`；
- 每个步骤的名称、状态、尝试次数、结果摘要和错误；
- 创建、开始、完成时间。

每次状态变化使用同目录临时文件和原子替换持久化。每个工作流由独立后台线程执行，状态写入由锁保护。

工作流启动时创建关联任务，并将任务 ID 注入每个步骤的参数。步骤事件继续写入现有任务存储。工作流结果只保存有界摘要和运行日志 ID，不复制完整命令输出。

步骤失败后按配置重试。最终成功时完成关联任务；最终失败时将任务标记为失败并记录步骤、错误和尝试次数。后续步骤不执行。

## 结果验证

`expect` 使用嵌套对象表示结果子集。例如：

```json
{"properties":{"ActiveState":"active"}}
```

验证函数递归比较对象；实际结果必须包含所有指定字段且值完全相同。数组或非对象不匹配时验证失败。

## 重启与错误处理

- 参数或步骤非法时，在创建后台线程前拒绝请求。
- 工作流启动失败时保留明确错误。
- `agentd` 重启时，不自动恢复仍在执行的线程；重启前处于 `queued` 或 `running` 的持久化工作流被标记为 `failed`，错误为 `agentd restarted while workflow was active`，关联任务同步标记失败。
- 单步失败不会终止 `agentd` 或其他工作流。
- 不实现跨重启继续执行，避免重复执行有副作用的步骤。

## 演示

### 成功工作流

Agent 提交高层目标和步骤：安装 heartbeat 配置、重启服务、验证 active、读取日志。`workflow.start` 返回后立即可用 `workflow.get` 查询。最终工作流和关联任务均为 `succeeded`，heartbeat 保持 active。

### 失败工作流

Agent 提交一个查询不存在但名称合法的 `agentos-missing.service` 的步骤。工作流必须将该步骤和关联任务标记为 `failed`，保留明确错误，不影响 heartbeat 或系统服务。

## 完成标准

- 工作流可以后台运行并持久化每一步状态。
- `expect` 能验证嵌套结果，失败时停止后续步骤。
- 成功演示产生完整工作流和任务记录。
- 失败演示产生明确错误和失败终态。
- agentd 重启能正确标记中断工作流。
- 实体机保持 agent2，systemd、SSH、agentd 和 heartbeat 正常。
