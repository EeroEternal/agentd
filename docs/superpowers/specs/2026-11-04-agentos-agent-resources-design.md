# AgentOS Agent 资源接口设计

## 目标

优化 Agent 在原生系统中的任务执行体验。不再要求本地界面或 CLI，重点提供可靠的结构化文件、软件包和网络接口，并全部接入现有任务及工作流。

## 文件接口

新增：

- `file.read`：读取绝对路径文本，限制 1 MiB，返回内容、大小和 SHA-256；
- `file.write`：原子写入文本，限制 1 MiB，可创建父目录和设置权限；
- `file.list`：列出目录项名称、类型、大小和权限；
- `file.delete`：删除单个文件或符号链接，不递归删除目录。

写文件前自动保存旧内容到 `/var/lib/agentd/backups/`，返回备份路径。新文件使用同目录临时文件和原子替换，避免部分写入。

## 软件包接口

新增：

- `package.info`：使用 `pacman -Qi` 查询单个软件包；
- `package.list`：使用 `pacman -Q` 查询已安装软件包；
- `package.install`：使用 `pacman -S --needed --noconfirm`；
- `package.remove`：使用 `pacman -Rns --noconfirm`。

包名和数量必须校验，所有命令通过现有执行器运行并记录完整日志。

## 网络接口

新增 `network.status`，返回：

- 接口及地址；
- 默认路由；
- `/etc/resolv.conf` 的实际目标；
- NetworkManager 设备状态。

NetworkManager 和 SSH 继续由现有原生监督器管理。

## 工作流

将文件、软件包和 `network.status` 加入工作流允许列表。文件接口和 `expect` 可以验证路径、内容和哈希，包及网络命令继续关联运行日志。

## 验证

1. 在临时目录验证原子写入、读取、目录列表和删除。
2. 在原生系统中写入 `/etc/agentos/node.json`，再通过工作流读取并验证内容。
3. 查询 `bash` 软件包信息和网络状态。
4. 确认任务、工作流及运行日志完整，原生 SSH 和 heartbeat 正常。

本阶段不修改内核、启动顺序或自动解锁配置。
