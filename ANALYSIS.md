# WASM Runtime 迁移分析

## 结论

a-Shell 原生 Python + 外部 WebUI 路线已判定不可行，不再作为产品路径。

失败原因不是 WebUI 启动方式，而是 Hermes Agent 的依赖闭包包含 `pydantic-core`。该模块是原生扩展，a-Shell 当前 Python 环境无法提供兼容版本，因此 Agent 无法完成启动。继续裁剪纯 Python 依赖、制作 zipapp 或依赖 a-Shell 预装模块，都不能解决这个硬阻塞。

新的产品边界是：

```text
a-Shell Python = launcher + 宿主能力
WASM Python    = Hermes Agent + Agent 依赖
```

## 版本来源

根仓库使用两个上游 submodule：

- `hermes-agent` → `https://github.com/NousResearch/hermes-agent.git`
- `hermes-webui` → `https://github.com/nesquena/hermes-webui.git`

当前固定提交：

- Hermes Agent: `3bef6b6a5c543c587b756e4091a8c3d0d66e1a6b`
- Hermes WebUI: `e36f77389191fe9d81cd3a7416772e2f7b022e19`

两个 submodule 的旧本地提交属于已经放弃的 a-Shell 原生 Python 适配，不再保留。

## 目标架构

```text
Safari / 本地客户端
        │ HTTP / WebSocket
        ▼
a-Shell 原生 Python launcher
        │
        ├── 127.0.0.1 listener
        ├── HTTP/WebSocket gateway
        ├── network capability
        ├── filesystem persistence
        ├── subprocess / PTY capability
        └── WASM runtime child
                │ stdin/stdout: framed RPC
                │ stderr: diagnostics
                ▼
        Pyodide 或 WASI CPython
                │
                ├── pydantic
                ├── pydantic-core WASM build
                ├── Hermes Agent
                └── 已验证的 WASM-compatible dependencies
```

WASM runtime 不监听外部网络端口。launcher 监听本机端口，并将 runtime 请求转化为宿主 capability。不能把普通 native Python 的 socket、subprocess、文件系统和终端语义直接假定为 WASM 中可用。

## Runtime 选择

需要在最小探针阶段比较两条路线：

### Pyodide

需要确认：

- 目标版本是否包含可用的 `pydantic` / `pydantic-core`
- `pydantic-core` 是否与 Pyodide 的 Emscripten ABI 匹配
- Hermes Agent 的 import 链是否能在 Pyodide 中完成
- stdio、文件系统和长时间运行模型请求是否能由 a-Shell launcher 稳定承载

### WASI CPython

需要确认：

- CPython-WASM 是否支持目标扩展加载方式
- `pydantic-core` 是否能构建为目标 WASI ABI
- 依赖闭包中是否存在只能使用 Emscripten/JavaScript API 的包
- a-Shell 的 WASM 执行能力是否支持双向 stdio、长时间运行和可靠终止

普通 CPython 的 `pydantic_core` native wheel 不能用于上述任一路线。目标必须是可被对应 WASM runtime 加载的扩展或兼容实现。

## stdio RPC 协议

stdin/stdout 是 runtime 与 launcher 之间唯一的控制通道，不能使用“任意文本行”作为协议边界。建议采用：

```text
4-byte big-endian unsigned length
UTF-8 JSON payload
```

消息必须带有请求 ID：

```json
{
  "type": "request",
  "id": 1,
  "method": "net.request",
  "params": {
    "url": "https://example.com",
    "method": "GET",
    "headers": {}
  }
}
```

响应：

```json
{
  "type": "response",
  "id": 1,
  "ok": true,
  "result": {
    "status": 200,
    "headers": {},
    "body": "..."
  }
}
```

流式输出使用独立 event 消息。stdout 禁止混入日志、banner 或 traceback；诊断输出写 stderr。

## 宿主能力

### 网络

先实现结构化 HTTP：

- `net.request`
- `net.stream`
- 请求超时和取消
- 最大响应体限制
- 目标地址限制
- secret 脱敏

不在第一版实现任意 TCP tunnel。模型 provider 和 WebUI 的实际请求需求优先于通用 socket 兼容性。

### 文件

runtime 使用虚拟文件系统，launcher 只持久化允许的目录：

```text
/home/hermes
/workspace
/tmp
```

需要区分只读资源、session/profile 持久化目录、workspace 和临时目录。禁止通过 capability 访问任意宿主绝对路径。

优先采用批量 checkpoint/sync，避免每一次 Python 文件操作都跨进程 RPC。

### 子进程

提供受限的 `process.spawn`：

- argv 数组而不是 shell 字符串
- 明确 cwd
- 受控环境变量
- 命令白名单
- 超时、取消和 kill
- 独立 stdout/stderr 流

缺少宿主支持时，Hermes 工具必须报告明确的 capability unavailable，而不是伪装成功。

### 终端

RPC stdio 与用户终端严格分离。需要终端时由 launcher 创建 pipe/PTY，并传递：

- `pty.open`
- `pty.write`
- `pty.resize`
- `pty.close`
- `pty.stdout` / `pty.stderr` events

第一版可以先实现非交互 pipe，待基础 Agent 路径稳定后再处理完整 PTY、prompt_toolkit 和 TUI。

## 迁移阶段

### Stage 0：清理旧路径

- 删除 a-Shell 原生 Python launcher、zipapp builder 和依赖检查器
- 删除旧的原生 WebUI 启动说明
- 重置两个 submodule 到远端最新提交
- 明确 a-Shell Python 不是 Agent runtime

### Stage 1：WASM bootstrap

- 启动目标 WASM Python
- 建立 framed stdio RPC
- 验证请求 ID、并发、错误、超时和 runtime 重启
- 确认 stdout/stderr 隔离

### Stage 2：原生扩展硬门槛

最小测试必须实际执行：

```python
import pydantic
from pydantic_core import SchemaValidator
```

并构造、执行一个真实 schema。仅成功 import `pydantic` 不算通过。

### Stage 3：Agent import

按顺序验证：

```text
配置加载
→ Hermes Agent 最小 import
→ provider client import
→ session/state
→ 一次模型请求
→ 流式输出
```

每一步都记录缺失模块、需要的 capability 和降级行为。

### Stage 4：WebUI gateway

将 WebUI 的网络入口放在 launcher 或明确的 WASM gateway 适配层中。不能让 WebUI 继续隐式 import 一个运行在 a-Shell 原生 Python 中的 Agent 实例。

### Stage 5：工具能力

按需接入文件、subprocess、PTY、MCP 和其他扩展，并为每项能力添加真实设备测试。

## 验收标准

一个阶段只有在以下条件满足后才能报告完成：

- 在真实目标环境中运行过，而不是只在桌面 Python 中运行
- `pydantic-core` 在目标 WASM runtime 中实际 import 并执行
- 大请求、大响应和二进制安全编码不会破坏 RPC framing
- 并发请求不会串包或错配 response ID
- runtime 崩溃、launcher 断开和请求超时都有可观察错误
- session checkpoint 后可以重启恢复
- 网络请求、文件写入和子进程都遵守 capability 边界
- 凭据不进入日志、URL、测试快照或发布归档

## 已删除的旧交付物

以下内容属于已放弃的 a-Shell 原生 Python 第一阶段，不再使用：

- `a-shell-check.py`
- `a-shell-cli.py`
- `a-shell-cli.sh`
- `a-shell-install-deps.sh`
- `a-shell-start.sh`
- `build-hermes.py`
- 原生 Python zipapp `hermes`
- `README-A-SHELL.md`
