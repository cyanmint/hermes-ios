# Hermes iOS WASM Runtime

本仓库为 iOS / a-Shell 提供 Hermes Agent 的 WASI Preview 1 运行时。正式运行路径只有一条：

```text
a-Shell 原生 Python
    └── loader.py
          └── stdio framed RPC
                └── wasm32-wasip1 CPython
                      ├── Hermes Agent
                      ├── 纯 Python 依赖
                      ├── 静态链接的 WASM 原生模组
                      ├── _socket facade
                      └── _ssl facade
```

a-Shell 原生 Python 运行 loader；loader 启动 WASI Preview 1 的 CPython WebAssembly runtime。Hermes Agent 不运行在 a-Shell 的宿主 Python 进程中。宿主 Python 负责真实 socket、TLS、DNS 和用户可见输出，WASM 只通过 stdin/stdout 与 loader 沟通。

## 运行时边界

### a-Shell 原生 Python 侧

`loader.py` 是宿主入口，负责：

- 启动 `wasm` 命令或本地 Wasmtime
- 管理 WASM 子进程的 stdin/stdout/stderr
- 解析 4 字节大端长度前缀的 JSON frame
- 执行 WASM 发出的 `socket.*` 与 `net.request` 请求
- 在宿主 Python 中执行 DNS、TCP、TLS 和 HTTP
- 将 WASM 的 `io.write` event 分类输出到 a-Shell stdout/stderr
- 在 WASM 退出后关闭全部代理 socket

loader 不把宿主 socket 对象或文件描述符暴露给 WASM。每个 socket 由 loader 保存，以整数 ID 通过 RPC 引用。

### WASI Preview 1 Python runtime 侧

最终 `hermes` 是 `wasm32-wasip1` CPython WebAssembly 程序。它必须由 loader 启动，不能直接作为 a-Shell 用户命令运行。

runtime 内部包含：

- CPython WASI runtime
- Hermes Agent 源码和纯 Python 依赖
- 目标 WASI ABI 编译并静态注册的原生模组，例如 `_pydantic_core`
- `wasi_loader.py`：WASM 侧 RPC 客户端
- `_socket.py`：将标准 socket 操作映射到 loader
- `_ssl.py`：将 TLS 操作映射到 loader
- `sitecustomize.py`：把 Python stdout/stderr 转成 `io.write` event

宿主 Python 的原生扩展不会被复制进 WASM。WASM 原生模组必须从源码为 `wasm32-wasip1` 编译、生成静态 archive、注册到 CPython，并在目标 runtime 中实际 import 和调用。

## stdio 通信协议

WASM 子进程的 stdout 不是普通文本流，而是唯一的 RPC 控制通道：

```text
4-byte big-endian payload length
UTF-8 JSON payload
```

WASM → loader 的请求：

```json
{
  "type": "request",
  "id": 1,
  "method": "socket.connect",
  "params": {
    "id": 3,
    "host": "example.com",
    "port": 443,
    "timeout": 30
  }
}
```

loader → WASM 的响应：

```json
{
  "type": "response",
  "id": 1,
  "ok": true,
  "result": {}
}
```

用户可见输出使用独立 event，不能直接写入 WASM stdout：

```json
{
  "type": "event",
  "event": "io.write",
  "stream": "stdout",
  "data": {
    "encoding": "base64",
    "data": "aGVsbG8K"
  }
}
```

loader 收到 event 后才将内容写入宿主 stdout 或 stderr。WASM 的 stderr 仍作为诊断通道由 loader 原样转发；RPC stdout 禁止混入日志、banner 或未 framing 的 traceback。

## socket 与 TLS 转发

WASI runtime 内的 `_socket.py` 提供 CPython `socket` 需要的最小兼容层：

```text
socket()
  → socket.open
connect()
  → socket.connect
send()/sendall()
  → socket.send/socket.sendall
recv()
  → socket.recv
getaddrinfo()
  → socket.resolve
close()
  → socket.close
```

`_ssl.py` 不链接 WASI preview1 socket API。`SSLContext.wrap_socket()` 发出 `socket.start_tls`，由 loader 使用 a-Shell 原生 `ssl` 在已建立的宿主 socket 上完成 TLS 握手。

当前 loader capability：

- `handshake`
- `io.write`
- `socket.hostname`
- `socket.resolve`
- `socket.open`
- `socket.connect`
- `socket.send` / `socket.sendall`
- `socket.recv`
- `socket.timeout`
- `socket.getopt` / `socket.setopt`
- `socket.name` / `socket.peer`
- `socket.shutdown`
- `socket.start_tls`
- `socket.close`
- `crypto.random`
- `net.request`

该路线提供出站网络能力，不等于向 WASM 暴露任意宿主路径、任意监听端口或任意文件描述符。

## 构建与交付

### 本地 WSL Arch Linux 构建

本项目当前以 WSL Arch Linux 作为构建环境。宿主准备以下路径：

```text
/root/hermes-build/cpython
/root/hermes-build/wasi-sdk-25.0-x86_64-linux
/root/hermes-build/wasmtime-v48/wasmtime
```

执行：

```sh
bash /mnt/w/_/hermes-ios/build-local-wasi.sh
```

脚本会创建干净的 `/root/hermes-build/loader-build`，保留 CPython Git 元数据，禁用原生 `_socket`/`_ssl`，并构建 `wasm32-wasip1` runtime。CPython 的 `wasi.py` 需要 Git 元数据来生成和校验构建信息，不能只解压没有 `.git` 的源码归档。

构建约束：

- `py_cv_module__socket=n/a`
- `py_cv_module__ssl=n/a`
- 不接受 preview1 `sock_*` imports
- 不复制宿主 `.so`、`.a`、`.dylib` 或 `.pyd` 作为 Python 扩展
- 原生模组必须经过目标源码构建、静态符号检查、CPython 注册和 WASM smoke test

### 启动

在 a-Shell 中：

```sh
python3 loader.py --wasm-command wasm hermes --help
```

在桌面 WSL 中使用 Wasmtime 验证 loader 路径：

```sh
python3 loader.py --wasm-command wasmtime hermes --version
```

不能使用以下方式作为正式启动路径：

```sh
wasmtime run hermes
wasm hermes
```

这些命令绕过 loader，因此不能提供 socket、TLS、DNS 和 stdio event 转发。

## 验证层级

完成交付前必须分别验证：

1. loader Python 语法和协议单元测试
2. `_socket`、`_ssl`、`wasi_loader` 语法检查
3. 最终 WASM magic 为 `\0asm`
4. 最终 WASM import section 不含 `wasi_snapshot_preview1:sock_*`
5. WASM 通过 loader 启动，而不是直接通过 Wasmtime 启动
6. WASM 能 import 并执行静态原生模组
7. `socket.getaddrinfo`、TCP connect、send/recv 和 TLS handshake 经 loader 完成
8. `print()` 和 stderr 经 `io.write`/诊断路径正确到达宿主
9. runtime 退出后 loader 回收 socket 和子进程

桌面 Python 测试只能验证协议和 loader 逻辑，不能替代 a-Shell 真实运行验证。

## 当前不属于本路线的内容

- 在 a-Shell 原生 Python 中直接 import Hermes Agent
- 把 host wheel 或 host `.so` 放入 WASM
- 让 WASM 直接调用 preview1 socket imports
- 直接运行 `hermes` WASM 文件绕过 loader
- 把 RPC 控制流和用户终端文本混在同一个未 framing 的 stdout
- 用 `net.request` 的成功响应冒充完整 socket/TLS 兼容性

详细协议、构建门槛和故障边界见 [`ANALYSIS.md`](ANALYSIS.md)。

## 来源

- `hermes-agent`：`NousResearch/hermes-agent`
- `hermes-webui`：`nesquena/hermes-webui`

根仓库当前固定 submodule 提交：

| Submodule | Commit |
| --- | --- |
| `hermes-agent` | `3bef6b6a5c543c587b756e4091a8c3d0d66e1a6b` |
| `hermes-webui` | `e36f77389191fe9d81cd3a7416772e2f7b022e19` |
