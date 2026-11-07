# AgentOS

> An experimental agent-native Linux runtime with natural-language task execution, LLM planning, headless browsing, and MCP tools.

AgentOS 是一个面向 AI Agent 的实验性操作系统控制层。用户可以用自然语言提交任务，由 LLM 规划器选择并调用系统、浏览器、文件和 MCP 工具，再将实际执行结果返回给用户。

AgentOS 不是把大模型放进内核，也不是另一个聊天界面。Linux 继续负责硬件、进程、网络和文件系统；AgentOS 在用户空间提供任务规划、工具调用、服务监督、健康检查和审计能力。

## 已实现能力

- **自然语言任务**：通过 `a do "..."` 提交真实任务，支持多轮工具调用和明确结束。
- **LLM 规划器**：支持 DeepSeek 及其他 OpenAI 兼容 `/chat/completions` 服务。
- **AgentCell 隔离执行**：普通命令默认在非特权 cell 中运行，使用 namespaces、Landlock、seccomp 和真实 cgroup 资源限制，默认无网络。
- **KVM 后端**：为需要独立内核的高风险任务提供 QEMU/KVM 启动接口，默认无网络、磁盘快照且不在硬件不可用时降级模拟。
- **交互式 VM**：通过 virtio-serial Guest Agent 执行命令，支持创建、启动、停止、执行、qcow2 快照、恢复和删除。
- **任务与文件**：支持 `a last`、`a files`、`a get`，可查看历史并将 workspace 文件下载到 Mac。
- **事务与恢复**：记录任务前工作区状态，支持 `a undo`、Btrfs 快照和 Limine 启动配置备份恢复。
- **显式权限**：普通任务默认禁止宿主特权操作、越界写入和自定义 VM 启动，按任务授予 capability。
- **运维工具**：提供安装、诊断、配对、更新、恢复及启动回归测试。
- **无头浏览器**：使用 Playwright 控制 Chromium，提取 JavaScript 渲染后的标题、正文和链接。
- **静态网页抓取**：去除 HTML、脚本和样式，返回纯文本。
- **MCP 工具**：支持 stdio 和 HTTP 传输、工具发现、调用、超时及进程清理。
- **filesystem MCP**：提供文件读写、目录、搜索、移动和复制等工具，限制在专用工作目录。
- **memory MCP**：提供持久实体、关系、观察记录和搜索能力。
- **系统控制**：管理 native 服务、查看健康状态、读写文件、执行命令和运行工作流。
- **Native 启动**：包含 PID 1、服务监督、Limine 启动项和自动 LUKS 解锁相关配置。
- **远程网关**：Mac 客户端通过带 Bearer Token 的 HTTP API 向 ThinkPad 提交任务，无需 SSH。

## 架构

```text
Mac: a do "task"
        |
        | authenticated HTTP
        v
AgentOS gateway (192.168.3.11:8765)
        |
        v
LLM planner <----> OpenAI-compatible model API
        |
        +----> cell_exec ----> drop-privilege launcher + cgroup
        |                      +----> AgentCell sand (no network)
        |
        +----> VM manager ----> QEMU/KVM + qcow2
        |                      +----> virtio-serial Guest Agent
        |
        +----> Local system tools ----> agentd Unix socket
        |
        +----> web_fetch / browser_fetch ----> Chromium + Playwright
        |
        +----> MCP stdio / HTTP ----> filesystem, memory, other MCP servers
        |
        v
Actual tool results -> final answer
```

## 快速使用

以下命令适用于当前已经部署好的 Mac + ThinkPad 环境，在 **Mac 终端**执行，无需 SSH。

### 系统状态

```bash
a status
a health
a services
```

### 自然语言与隔离 cell

```bash
a "总结 xinference.cn，保存成报告"
a cell python 'print(6 * 7)'
a cell exec uname -r
```

cell 内的工作目录 `/home/agent` 对应宿主 `/var/lib/agentos/workspace`。默认无网络，限制为 1 GiB 内存、1 个 CPU 核和 128 个 PID。

### 交互式 KVM 虚拟机

```bash
a vm smoke
a vm create dev
a vm start dev
a vm exec dev uname -r
a vm stop dev
a vm snapshot dev clean
a vm restore dev clean
a vm delete dev
```

`vm_run` 仍保留给需要明确 kernel 和 raw disk 的高级任务。其他主机需要先在 BIOS 中启用 Intel VT-x/AMD-V 并出现 `/dev/kvm`。

`limine/agentos-kvm.conf` 使用完整 Arch 内核；`limine/agentos-luks-mkinitcpio.conf` 将密钥打包进 initramfs，在保留 LUKS 加密的同时实现自动解锁。

### 历史、文件和撤销

```bash
a last
a files
a get report.md
a undo
```

`a undo` 只回滚可逆任务的工作区变化；包含宿主特权修改的任务会明确拒绝回滚，而不是给出虚假的成功状态。

### Btrfs 快照与启动恢复

```bash
a snapshot create clean
a snapshot restore clean   # 设置下次启动到该快照，不自动重启
a snapshot delete clean
a boot default
```

### 运维

```bash
a doctor
a pair                   # 显示 Mac 配对命令
agentos install         # 在 ThinkPad 上执行
agentos update
agentos recover
```

普通自然语言任务默认没有宿主特权。确有需要时显式授权：

```bash
a do --allow-host "使用 exec 执行 id"
a do --allow-vm "使用 vm_run 启动指定的测试镜像"
```

### 获取动态网页正文

```bash
a do "Fetch https://xinference.cn with the headless browser, remove HTML, and return the main text"
```

### 研究并保存报告

```bash
a do "Visit https://xinference.cn, summarize its four main products, and use filesystem MCP to save the summary at /var/lib/agentos/workspace/summary.md"
```

### 读取已保存的报告

```bash
a do "Use filesystem MCP to read /var/lib/agentos/workspace/summary.md and return its contents"
```

### 保存并查询记忆

```bash
a do "Use memory MCP to remember that I prefer concise English reports"
a do "Use memory MCP to find my report preferences"
```

### 组合浏览器、文件和记忆

```bash
a do "Read https://xinference.cn with the browser, save a five-point summary at /var/lib/agentos/workspace/research.md using filesystem MCP, and use memory MCP to remember that this is my current research topic"
```

## ThinkPad 本地 CLI

在 AgentOS 主机上可以直接使用 `agentos`：

```bash
sudo agentos status
sudo agentos health
sudo agentos services
sudo agentos restart heartbeat
sudo agentos logs heartbeat 50
sudo agentos exec uname -r
```

其他命令：

```text
agentos start|stop|restart <service>
agentos read <path>
agentos write <path> <text>
agentos restore <path>
agentos workflow <json-file>
agentos workflow-status <workflow-id>
```

## 配置

运行时配置位于 ThinkPad 的 `/etc/agentd`，不会提交到仓库。

### LLM：`/etc/agentd/llm.json`

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key": "<your-api-key>",
  "mock": false
}
```

### MCP：`/etc/agentd/mcp.json`

```json
{
  "servers": {
    "filesystem": {
      "command": "/usr/bin/mcp-server-filesystem",
      "args": ["/var/lib/agentos/workspace"]
    },
    "memory": {
      "command": "/usr/bin/mcp-server-memory",
      "env": {
        "MEMORY_FILE_PATH": "/var/lib/agentos/memory.json"
      }
    }
  }
}
```

网关 Token 保存在 `/etc/agentd/gateway.token`；Mac 副本保存在 `~/.config/agentos/token`。这些文件应使用 `0600` 权限。

## 项目结构

```text
agentd/                 AgentOS 核心、规划器、浏览器、MCP 与系统资源
bin/agentos             ThinkPad 本地命令行工具
agentd_gateway.py       远程 HTTP 任务网关
agentd_webserver.py     示例 HTTP 服务
agentd_mock_mcp.py      MCP 协议验证用模拟服务
kernel/                 实验性内核入口
limine/                 Limine 启动配置
systemd/                agentd 服务配置
workflows/              可执行任务工作流
docs/superpowers/       设计与实现记录
```

## 验证

运行 Python 回归测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

对全部 Python 入口执行语法检查：

```bash
python3 -m py_compile \
  agentd/*.py \
  agentd_gateway.py \
  agentd_mock_mcp.py \
  agentd_webserver.py \
  bin/agentos bin/a
```

真实启动 smoke 在目标主机运行，覆盖默认启动项、cell Python/ctypes 和 KVM Guest Agent：

```bash
sudo PYTHONPATH=/usr/local/lib/agentd python3 tests/boot_smoke.py
```

端到端验证应检查实际工具结果，而不能只检查模型生成的汇报。

## 安全边界

- AgentOS 能执行高权限系统操作，建议使用专用测试机或虚拟机。
- 普通命令应使用 `cell_exec`；`exec` 是明确的特权宿主管理通道。
- cell 启动器只在加入 cgroup 和降权阶段使用 root，payload 以 `agentcell` 用户运行。
- filesystem MCP 默认仅开放 `/var/lib/agentos/workspace`。
- `vm_run` 默认使用临时磁盘快照；`persist=true` 会修改 Guest 镜像，应谨慎使用。
- 不要把 LLM API Key、网关 Token、LUKS 密钥或 SSH 私钥提交到仓库。
- 对删除、覆盖、软件包变更等不可逆操作，应增加人工确认和回滚策略。
- 浏览器工具会执行目标页面自身的 JavaScript，不应访问不可信的管理凭据或敏感浏览器会话。

## 当前状态与限制

AgentOS 已在 Lenovo ThinkPad X240 的 Arch Linux 环境中完成 native 启动、真实 cell 隔离、内核 cgroup 限制和自然语言任务验证。交互式 KVM VM 已验证命令执行、快照恢复和正常关机；工作区撤销、Btrfs 快照、显式权限、安装更新和恢复流程也已通过真实测试。

当前提供 `bin/agentos-install` 安装脚本和启动恢复配置，但仍属于实验项目，**不提供跨硬件发行版镜像或生产级安全保证**。

后续适合继续完善：更多凭据隔离的 MCP 服务、cell 网络白名单、VM 网络与共享目录、长期任务调度和更广泛的硬件回归测试。
