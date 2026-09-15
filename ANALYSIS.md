# loader + WASI Preview 1 技术路线

## 1. 结论

Hermes Agent 的正式运行边界是：

```text
a-Shell 原生 Python
  └─ loader.py
       ├─ 启动 wasm32-wasip1 CPython
       ├─ 通过 stdin/stdout 处理 framed RPC
       ├─ 在宿主执行 DNS / TCP / TLS / HTTP
       └─ 将 WASM 输出分类到 stdout / stderr

wasm32-wasip1 CPython
  ├─ Hermes Agent
  ├─ pydantic 与纯 Python 依赖
  ├─ 静态链接的 WASM 原生模组
  ├─ _socket → loader socket capability
  ├─ _ssl → loader TLS capability
  └─ sitecustomize → loader io.write event
```

这条路线同时解决两个独立问题：

1. Hermes 可以在 WASI CPython 内加载目标 ABI 的原生模组，例如静态注册的 `_pydantic_core`
2. Hermes 可以使用 socket，但 socket 由 a-Shell 原生 Python 侧的 loader 执行，而不是由 WASM 直接导入 WASI preview1 socket API

因此，“Hermes 能加载原生模组”和“WASM 能访问网络”不是同一层的功能：前者发生在 WASM runtime 的 CPython 链接边界，后者发生在 loader capability 边界。

## 2. 进程与通道边界

### 2.1 loader 是唯一入口

用户只启动 a-Shell 原生 Python 的 `loader.py`，不需要知道 WASM 执行器或
artifact 路径：

```sh
python3 loader.py
python3 loader.py --version
python3 loader.py model
```

loader 再启动 WASM：

```text
loader.py
  └─ subprocess: wasm hermes
```

loader 自动定位同目录的 `hermes`，并自动发现 a-Shell bundle 中的 `wasm`。
桌面验证会自动使用 PATH 中的 `wasmtime`。`hermes` 文件本身不是用户入口；
直接运行它会绕过所有网络和 stdio 转发能力，不能作为通过标准的运行方式。

### 2.2 stdin/stdout 是 framed RPC

WASM 子进程的 stdout 不允许出现普通文本。每一帧为：

```text
uint32 big-endian length
UTF-8 JSON payload
```

WASM 侧的 `wasi_loader.call()`：

1. 分配递增 request ID
2. 写入 `type=request` frame
3. flush stdout
4. 从 stdin 读取完整 response frame
5. 校验 response ID 和 `ok`
6. 把 loader error 转成 Python `OSError`

loader 侧的 `serve_child()`：

1. 从 WASM stdout 读取 frame
2. 将 `request` 分派给 capability
3. 将 response frame 写回 WASM stdin
4. 将 `event/io.write` 写到用户 stdout/stderr
5. WASM 退出后关闭所有宿主 socket

### 2.3 输出分类

输出来源必须明确分类：

| 来源 | WASM/loader 表现 | 最终去向 |
| --- | --- | --- |
| `print()` / `sys.stdout.write()` | `event/io.write`, `stream=stdout` | a-Shell stdout |
| `sys.stderr.write()` | `event/io.write`, `stream=stderr` | a-Shell stderr |
| runtime 原始 stderr | loader 的 stderr forwarding thread | a-Shell stderr |
| RPC request/response | length-prefixed JSON | 仅 loader 与 WASM 内部 |
| socket/TLS bytes | response 中 base64 | 仅 loader 与 WASM socket facade |

日志不能写入 RPC stdout。否则下一帧的 4 字节长度会被破坏，runtime 与 loader 会失去同步。

## 3. WASM 侧 Python 运行时

### 3.1 CPython 配置

CPython 编译目标为：

```text
wasm32-wasip1
```

原生 `_socket` 和 `_ssl` 必须从 CPython target build 中禁用：

```text
py_cv_module__socket=n/a
py_cv_module__ssl=n/a
```

同时 `Modules/Setup.local` 不能启用这两个会产生宿主网络 imports 的模块。

### 3.2 原生模组加载

WASM 侧原生模组的允许路径是：

```text
source
  → target wasm32-wasip1 static archive
  → PyInit_* symbol check
  → CPython builtin registration
  → import in final python.wasm
  → real API operation
```

以 `_pydantic_core` 为例：

- 源码使用 WASI target 编译
- 产物必须是目标静态 archive
- 必须存在 `PyInit__pydantic_core`
- CPython 启动入口必须调用 `PyImport_AppendInittab`
- pydantic 的纯 Python wrapper 必须保留
- 最终 smoke test 必须构造并执行 `SchemaValidator`

禁止将 Linux/macOS/iOS 的 `.so`、`.dylib`、`.pyd` 或 host wheel 复制到 artifact。那样既不能证明 WASI ABI 兼容，也会把宿主平台依赖带进发布物。

### 3.3 socket facade

WASI `Lib`/site-packages 中的 `_socket.py` 是纯 Python facade。它不调用宿主 socket，也不依赖 preview1 `sock_*` imports。

主要映射：

| Python 操作 | RPC method |
| --- | --- |
| `socket()` | `socket.open` |
| `getaddrinfo()` | `socket.resolve` |
| `connect()` | `socket.connect` |
| `send()` | `socket.send` |
| `sendall()` | `socket.sendall` |
| `recv()` | `socket.recv` |
| `settimeout()` | `socket.timeout` |
| `getsockopt()` | `socket.getopt` |
| `setsockopt()` | `socket.setopt` |
| `getsockname()` | `socket.name` |
| `getpeername()` | `socket.peer` |
| `shutdown()` | `socket.shutdown` |
| `close()` | `socket.close` |

WASM 只持有 socket ID；真实 `socket.socket` 对象保存在 loader 的 `sockets` 表中。

### 3.4 ssl facade

WASI `_ssl.py` 同样是纯 Python facade。`SSLContext.wrap_socket()` 发出：

```json
{
  "type": "request",
  "id": 8,
  "method": "socket.start_tls",
  "params": {
    "id": 3,
    "server_hostname": "example.com",
    "verify_mode": 2,
    "check_hostname": true
  }
}
```

loader 使用 a-Shell 原生 Python 的 `ssl` 模块在对应宿主 socket 上执行 TLS。证书验证、SNI 和握手错误因此属于 loader 的宿主行为；WASM 不链接 OpenSSL，也不产生 WASI socket imports。

## 4. loader capability 协议

### 4.1 握手

WASM 启动后可以请求：

```json
{"type":"request","id":1,"method":"handshake","params":{}}
```

loader 返回：

```json
{
  "type": "response",
  "id": 1,
  "ok": true,
  "result": {
    "protocol": 2,
    "capabilities": ["io.write", "socket", "ssl", "net.request"]
  }
}
```

### 4.2 限制

loader 当前实施以下边界：

- 单帧最大 4 MiB
- socket 单次读写最大 1 MiB
- HTTP request body 最大 2 MiB
- HTTP response body 最大 8 MiB
- HTTP timeout 最大 300 秒
- URL 只允许 `http` / `https`
- URL 禁止凭据和 fragment
- HTTP body 使用 UTF-8 或显式 base64
- 未知 socket ID 拒绝执行
- 不支持的 capability 返回明确错误
- socket 不在日志中输出凭据或完整敏感 payload

当前实现是出站 socket capability，不提供任意宿主路径访问、任意文件描述符传递或 WASM 监听端口。

## 5. 本地 WSL Arch 构建

当前验收不依赖 GitHub Actions。WSL Arch 应准备：

```text
/root/hermes-build/cpython
/root/hermes-build/wasi-sdk-25.0-x86_64-linux
/root/hermes-build/wasmtime-v48/wasmtime
```

仓库脚本：

```sh
bash /mnt/w/_/hermes-ios/build-local-wasi.sh
```

脚本行为：

1. 删除并重建 `/root/hermes-build/loader-build`
2. 从 CPython Git HEAD 导出源码
3. 保留 `.git`，因为 `Tools/wasm/wasi.py` 需要 Git 元数据
4. 写入 `Modules/Setup.local`
5. 设置 WASI SDK 与 Wasmtime PATH
6. 执行 CPython host build
7. 执行 CPython `wasm32-wasip1` target build

本地 build failure 必须按边界分类：

| 错误 | 检查点 |
| --- | --- |
| `wasmtime not found` | `PATH` 是否包含 `/root/hermes-build/wasmtime-v48` |
| `fatal: not a git repository` | loader-build 是否保留 `.git` |
| `_socket` / `_ssl` preview1 imports | Setup.local 或 config cache 是否复用了旧配置 |
| 找不到静态 archive | target Rust/Cargo 构建和 crate archive 名称 |
| 找不到 `PyInit_*` | native extension 未按 builtin contract 构建 |
| WASM 启动但不能 import facade | facade 是否复制到 `lib/python3.13/`，而不是只放在 artifact 根目录 |
| loader 无 response | runtime 是否被直接启动、stdout 是否有未 framing 输出 |

## 6. artifact 组装

最终运行目录至少需要：

```text
hermes                         # wasm32-wasip1 CPython artifact
loader.py                      # a-Shell 原生 Python 入口
lib/python3.13/sitecustomize.py
lib/python3.13/wasi_loader.py
lib/python3.13/_socket.py
lib/python3.13/_ssl.py
lib/python3.13/site-packages/
hermes-agent/
hermes-webui/
manifest.json
```

`sitecustomize.py`、`wasi_loader.py`、`_socket.py` 和 `_ssl.py` 必须安装到 CPython WASI 默认的 `/lib/python3.13` module search path（artifact 中对应 `lib/python3.13/`）。仅复制到 `/` 或 artifact 外层不会使 WASM import 到这些模块。

manifest 必须声明：

```json
{
  "target": "wasm32-wasip1",
  "loader_required": true,
  "forwarded_capabilities": ["io.write", "socket", "ssl"]
}
```

## 7. 验证顺序

### 静态验证

```sh
python3 -m py_compile loader.py wasi_loader.py sitecustomize.py
python3 -m py_compile wasi_runtime/_socket.py wasi_runtime/_ssl.py
python3 -m unittest discover -s tests -v
git diff --check
```

最终 WASM 解析 import section，拒绝：

```text
wasi_snapshot_preview1:sock_accept
wasi_snapshot_preview1:sock_bind
wasi_snapshot_preview1:sock_connect
wasi_snapshot_preview1:sock_recv
wasi_snapshot_preview1:sock_send
wasi_snapshot_preview1:sock_shutdown
```

字节串搜索只能作为辅助检查，不能代替 WASM import section 解析。

### loader 验证

至少验证：

1. handshake protocol 2
2. `io.write` stdout/stderr 分类
3. DNS resolve
4. TCP connect
5. send/recv
6. TLS start and certificate policy
7. unknown socket ID error
8. frame size rejection
9. child exit 后 socket cleanup

### runtime 验证

在 loader 下执行：

```sh
python3 loader.py --version
```

再执行原生模组和网络联合探针：

```python
import socket
import ssl
import pydantic
from pydantic_core import SchemaValidator

print(socket.getaddrinfo("example.com", 443))
print(SchemaValidator({"type": "str"}).validate_python("hermes"))
```

直接执行以下命令不算通过：

```sh
wasmtime run hermes
wasm hermes
```

因为这两个路径都绕过 loader。

## 8. 失败处理与状态报告

报告必须分开写：

- 构建是否成功
- 最终 WASM 是否无 preview1 socket imports
- 原生模组是否真实 import 和 API 调用
- loader protocol 是否通过
- socket/DNS/TLS 是否通过
- 本地 Wasmtime 结果
- a-Shell 真机结果

“Python 启动成功”“WASM magic 正确”“`--version` 成功”都不能单独证明 socket 或 TLS 已经可用。桌面 Wasmtime 成功也不能替代 iPad a-Shell 验证；二者必须分别记录。

## 9. 不再采用的路线

以下路线与当前架构冲突：

- 在 a-Shell 原生 Python 中直接运行 Hermes Agent
- 直接运行 WASM 绕过 loader
- CPython 自动构建原生 `_socket` / `_ssl`
- 将 preview1 socket imports 当作网络实现
- 复制 host wheel 或宿主动态库到 WASM
- 把普通日志写进 framed RPC stdout
- 只实现 `net.request` 却宣称完整 socket/TLS 兼容
