# Hermes iOS WASM Runtime

本项目为 iOS/a-Shell 设计 Hermes Agent 的 WASM 运行时，不再尝试让 Agent 直接运行在 a-Shell 自带的 `python3` 中。

## 为什么迁移到 WASM

原生 Python + 外部 WebUI 路线已经验证不可行：Hermes Agent 依赖 `pydantic`，而 `pydantic-core` 是原生扩展模块，a-Shell 当前 Python 环境无法提供兼容模块，Agent 因此无法启动。

所以本项目的运行边界是：

```text
a-Shell 原生 Python
└── launcher / 宿主能力代理
    └── stdio framed RPC
        └── WASM Python runtime
            └── Hermes Agent + WASM-compatible dependencies
```

a-Shell 的 Python 只负责启动 runtime、监听本机端口和提供宿主能力；Hermes Agent 及其依赖必须在 WASM Python runtime 内运行。

## 当前来源

- `hermes-agent`：`NousResearch/hermes-agent`
- `hermes-webui`：`nesquena/hermes-webui`

当前根仓库固定的 submodule 提交：

| Submodule | Commit |
| --- | --- |
| `hermes-agent` | `3bef6b6a5c543c587b756e4091a8c3d0d66e1a6b` |
| `hermes-webui` | `e36f77389191fe9d81cd3a7416772e2f7b022e19` |

## 运行时架构

```text
Safari / 本地客户端
        │ HTTP / WebSocket
        ▼
a-Shell launcher
        ├── 127.0.0.1 listener
        ├── network capability broker
        ├── filesystem persistence broker
        ├── subprocess / PTY broker
        └── WASM runtime child process
                │ stdin/stdout: framed RPC only
                │ stderr: diagnostics
                ▼
        Hermes Agent
```

WASM runtime 不直接监听网络端口。它通过结构化 stdio RPC 请求网络、文件、子进程和终端能力。launcher 执行请求并返回结果。

## 第一阶段目标

第一阶段不是移植完整 UI，而是建立可验证的 WASM Python 基础：

1. 启动 Pyodide 或 WASI CPython runtime
2. 建立双向 framed stdio RPC
3. 让 `pydantic` 和 `pydantic-core` 在目标 runtime 中成功 import
4. 执行 `SchemaValidator` 的实际验证
5. 导入 Hermes Agent 的最小启动路径
6. 验证 launcher 的错误、超时、取消和 runtime 重启行为

`pydantic-core` 必须是目标 WASM ABI 的扩展或兼容实现。普通 macOS、Linux、iOS `.so`/`.dylib`/`.pyd` 文件不能放入 WASM runtime。

## 宿主能力边界

### 网络

初版提供结构化 HTTP 请求能力，而不是透明 TCP 转发：

- `net.request`
- `net.stream`
- 后续按需增加 WebSocket

launcher 负责超时、取消、请求大小、目标限制、Origin/token 校验和 secret 脱敏。

### 文件

WASM 使用受限的虚拟工作区。持久化内容通过 checkpoint/sync 写入 a-Shell 包目录或用户指定的可写目录，不向 runtime 暴露任意绝对路径。

### 子进程

子进程必须通过受限的 `process.spawn` capability 启动，使用 argv、白名单、cwd、环境变量和超时限制；不接受未经解析的任意 shell 字符串。

### 终端

launcher 在 a-Shell 侧管理 pipe 或 PTY。runtime 的 RPC stdio 与交互终端严格分离，不能把日志或终端输出混入 RPC stdout。

## 当前不提供的内容

- a-Shell 原生 Python 中直接启动 Hermes Agent
- 纯 Python zipapp 作为 Agent 运行时
- 依赖 a-Shell 已安装原生扩展的兼容性假设
- 未经验证的完整 Hermes CLI、TUI、MCP、浏览器、语音和媒体功能

## 开发原则

- 所有 runtime 依赖都必须在目标 WASM runtime 内验证
- 宿主能力通过明确的、可审计的 capability RPC 暴露
- RPC 使用长度前缀 framing，不依赖换行分隔 JSON
- stdout 只承载 RPC；诊断信息写入 stderr
- 不把 token、API key 或 OAuth 凭据写入日志、归档或测试输出
- 每个阶段都必须有真实 a-Shell 验证，桌面 Python 测试不能替代设备验证

详细设计和迁移顺序见 [`ANALYSIS.md`](ANALYSIS.md)。

## WASI 命令交付物

GitHub Actions 成功构建后会生成直接的 WASI 命令：

```text
./loader.py hermes
```

WASM 不是可直接执行的用户入口，必须由同一 artifact 中的 `loader.py` 启动。loader 为 WASM 提供唯一的 framed stdin/stdout 通道，并把应用输出分类转发到 a-Shell 的 stdout/stderr；`_socket` 和 `_ssl` 是 WASI 侧的纯 Python facade，实际 DNS、TCP 和 TLS 操作均由 loader 在 a-Shell 原生 Python 中执行。

```sh
python3 loader.py --wasm-command wasm hermes --help
```

a-Shell 中必须运行 loader，而不是直接运行 `hermes`。loader 退出时会关闭所有代理 socket；协议 stdout 只承载 framed RPC，用户可见输出由 `io.write` event 还原到 stdio。

## WASI capability broker

WASI CPython 不直接拥有网络 socket、TLS、任意宿主路径或任意子进程能力。根目录的 `wasi_capability_broker.py` 提供方案 1 的第一阶段宿主 broker：使用 4 字节大端长度前缀的 JSON RPC，并实现受限的 `net.request` capability。请求和响应均包含 `type` 与 `id`；诊断只写入 stderr，不污染 RPC stdout。

当前 loader 已实现请求体、响应体、超时、URL scheme、URL 凭据、重复 request id 和最大 frame 限制，并提供 `socket.*`/`ssl` 转发。它同时负责将 `io.write`、网络响应和诊断信息分类到 a-Shell 的 stdout、stderr 或原生 socket；WASM 本身不导入 WASI preview1 socket API。

本地协议单元测试：

```sh
python3 -m unittest -v tests.test_wasi_capability_broker
```

## a-Shell 远程调试工具

根目录的 `ashell-debug-server.py` 是纯 Python 标准库调试代理，支持：

- 执行 argv 命令并实时转发 stdout/stderr
- 上传和下载文件
- 限制工作根目录，拒绝 `..` 越界路径
- 密码认证

a-Shell 中运行：

```sh
python3 ashell-debug-server.py \
  --host 0.0.0.0 \
  --port 8765 \
  --root . \
  --password '仅用于本次调试的密码'
```

另一台机器上使用 `ashell-debug-client.py`：

```sh
python3 ashell-debug-client.py --host IPAD_IP --port 8765 \
  --password '仅用于本次调试的密码' \
  exec -- python3 -c 'import sys; print(sys.version)'

python3 ashell-debug-client.py --host IPAD_IP --port 8765 \
  --password '仅用于本次调试的密码' \
  upload local.txt remote.txt
```

默认服务端绑定 `127.0.0.1`；监听 LAN 时必须设置密码。协议没有 TLS，密码和调试数据只应在可信网络或 VPN/SSH 隧道中传输，调试结束后用 `Ctrl-C` 停止服务。
