# Hermes iOS / a-Shell 项目笔记

> 本文件记录当前项目的架构、构建、部署、调试和已知阻塞。不得在此记录密码、token、私钥、连接字符串或其他凭据。

## 1. 项目目标

本项目的目标是在 iPad 的 a-Shell 环境中运行原始 Hermes WASM，并提供可工作的 Hermes WebUI。交付物不是一个启动器替代品，而是 a-Shell 专用的原始 WASM：

```text
hermes.wasm
```

它必须由 a-Shell 的 shell dispatch 启动，例如：

```sh
./loader.py webui
```

loader 负责启动 a-Shell 的 WASM 执行路径、转发 framed stdio，并为 WASI 程序提供 socket/TLS capability broker。普通 Windows Wasmtime 不能验证该 WASM，因为它包含 a-Shell 专用的：

```text
wasi_snapshot_preview1::ashell_system
```

## 2. 当前仓库和主要交付物

工作目录：

```text
W:/_/hermes-ios
```

主要文件：

```text
loader.py
asdbd.py
hermes.wasm
hermes-runtime.zip
overlay/hermes/hermes_cli/webui.py
build/build-local-wasi.sh
build/package-hermes.sh
overlay/python/wasi_loader.py
overlay/python/sitecustomize.py
overlay/python/wasi_runtime/_ssl.py
```

运行时采用单个 ZIP：

```text
lib/python3.13/
lib/python3.13/site-packages/
```

ZIP 必须保持 `ZIP_STORED`。目标 CPython 早期启动阶段可能尚未具备解压缩能力；使用压缩 ZIP 会导致非常早期的导入失败。

运行时必须包含完整标准库、`encodings`、Hermes CLI、Hermes Agent、WebUI 源码、WebUI 静态资源及其依赖。不能只打包精简 loader staging tree。

## 3. 架构边界

### WASM 和 loader

- `hermes.wasm` 是 a-Shell 专用原始 WASM。
- loader 必须通过 a-Shell shell dispatch 启动 WASM，不能用普通 `Popen(["./hermes.wasm"])` 绕过 a-Shell 执行器。
- Python/WASM 使用结构化 framed 输入输出。
- 协议 JSON 不能被普通日志污染。
- 普通诊断输出到 stderr。
- `stdio.output/stdout` 转发到宿主 stdout。
- `stdio.output/stderr` 转发到宿主 stderr。
- `socket.data` 不得污染 stdout。

### Capability broker

WASI 程序不使用 WASI preview1 的原生 socket imports。socket 和 TLS 请求通过 loader/RPC capability broker 完成。标准库 socket 已切换到 WASI socket facade，并补充了 `AI_PASSIVE` 等标准常量。

WebUI 子进程的 stdin 不能过早关闭：该 pipe 同时承担 capability broker 的 response 回写。WebUI 本身不需要用户交互输入，但 broker pipe 必须保持打开直到子进程退出。

### 线程和退出生命周期

loader 已处理以下问题：

- stdin forwarding 使用可中断的 `select.select()`，避免阻塞线程永久存活。
- 非交互模式也不能使用无法收尾的 daemon stdin 线程。
- stderr forwarding 使用可收尾线程。
- WASM 退出后停止 stdin forwarding，但不关闭宿主终端 stdin。
- WebUI WASM 退出时报告退出码；没有输出就退出时也应给出诊断。

## 4. CPython/WASI 构建

固定 WASI SDK：

```text
/root/hermes-build/wasi-sdk-ashell-22
```

真实 zlib 通过 CPython C extension 构建，不使用 Python shim：

```text
Modules/zlibmodule.c
/root/hermes-build/zlib-wasi/libz.a
```

`build/build-local-wasi.sh` 会把 zlib 静态链接到 CPython/WASI，并注册真实的 `zlib` builtin。最终 runtime 中不能残留会遮蔽真实 builtin 的 `zlib.py` 或 gzip fallback。

构建和打包之间存在 artifact handoff。`build/package-hermes.sh` 消费的是：

```text
/root/hermes-build/hermes-artifact/python.wasm
```

完整重新打包前，必须把最新构建产生的 `python.wasm` 同步到该路径。曾遇到：

```text
missing WASM source artifact: /root/hermes-build/hermes-artifact/python.wasm
```

因此当前如果该 artifact 尚未恢复，不能声称已经完成完整重建。

建议的 WSL 构建流程：

```sh
cd /mnt/w/_/hermes-ios
SKIP_SOURCE_FETCH=1 bash build/build-local-wasi.sh
cp /root/hermes-build/loader-build/cross-build/wasm32-wasip1/python.wasm \
   /root/hermes-build/hermes-artifact/python.wasm
SOURCE_ARTIFACT=/root/hermes-build/hermes-artifact \
CPYTHON_SOURCE=/root/hermes-build/loader-build \
   bash build/package-hermes.sh
```

构建后至少验证：

```sh
python -c 'from pathlib import Path; p=Path("hermes.wasm"); assert p.read_bytes()[:4] == bytes.fromhex("0061736d"); print(p.stat().st_size)'
unzip -t hermes-runtime.zip
```

还要检查 runtime 中存在 `encodings/`、`hermes_cli/`、`openai/`、`wasi_runtime/__init__.py` 及真实 zlib 相关符号/模块。

## 5. TLS/WebUI 兼容性

WASI TLS facade 位于：

```text
overlay/python/wasi_runtime/_ssl.py
```

已处理：

- ALPN 参数可能是标准库传入的长度前缀 `bytearray`，不能直接执行 `bytes(protocol, "ascii")`。
- `SSLContext.post_handshake_auth` 属性需要存在，否则 urllib3/标准库会触发 `AttributeError`。
- framed stderr 的 `_Stream` 不一定实现 `fileno()`，因此 `faulthandler` 只能在 stderr 支持 `fileno()` 时启用。

设备曾显示：

```text
NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+,
currently the 'ssl' module is compiled with 'a-Shell loader TLS'
```

这目前是兼容性警告，不应直接当作崩溃根因。

## 6. WebUI 集成和启动日志

WebUI 入口通过 Hermes CLI 的 `webui` 子命令注册。主要 launcher：

```text
overlay/hermes/hermes_cli/webui.py
```

该 launcher 已加入 verbose 启动阶段日志、traceback 和条件式 faulthandler。loader 为 WebUI 设置可写目录：

```text
HERMES_WEBUI_DEFAULT_WORKSPACE=workspace
HERMES_WEBUI_STATE_DIR=webui-state
HERMES_HOME=hermes-home
```

WebUI 源码的 workspace 初始化曾失败：

```text
RuntimeError: Could not create or access any usable workspace directory.
Set HERMES_WEBUI_DEFAULT_WORKSPACE to a writable path.
```

通过上述环境变量修复。

曾出现：

```text
ValueError: write to closed file
```

根因是 loader 错误关闭 WebUI 子进程 stdin，而 capability broker 仍需要向该 pipe 写回 response。现在必须保持 child stdin 打开。

ZIP 追加同名 entry 不是覆盖。向旧 ZIP 追加新的 `webui.py` 可能导致 zipimport 仍读取旧 entry。因此修复 runtime 时必须重建 ZIP，确保每个路径只有一个 entry，不能简单 append 同名文件。

loader 支持选择 runtime archive：

```text
HERMES_RUNTIME_ARCHIVE=...
```

但 CPython/WASI 启动早期仍可能依赖固定的 `hermes-runtime.zip` 路径。设备调试时优先把已验证的 runtime 放回固定文件名。

## 7. 当前设备部署方式

设备信息：

```text
iOS 16.3.1
UDID: 00008110-001C28910184801E
bundle id: AsheKube.app.a-Shell
Developer Mode: true
```

设备 Documents 目录的有效工作根为：

```text
~/Documents
```

或对应的 sandbox 路径：

```text
/private/var/mobile/Containers/Data/Application/A258E112-64C2-4928-A48C-0B4AA8A38AE6/Documents
```

设备调试和命令执行必须使用 `asdbd.py`；不要使用 SSH。`pymobiledevice3` 用于 USB House Arrest 文件传输和回读。

House Arrest 上传路径必须包含 `Documents/`，例如：

```sh
uv tool run pymobiledevice3 --no-color apps push \
  --documents AsheKube.app.a-Shell \
  loader.py Documents/loader.py

uv tool run pymobiledevice3 --no-color apps push \
  --documents AsheKube.app.a-Shell \
  hermes-runtime.zip Documents/hermes-runtime.zip
```

使用 `--documents` 时把目标写成根路径会触发 AFC status 10；必须使用 `Documents/<name>`。

文件传输后，House Arrest 创建的文件可能是 `0644`。执行权限和状态应通过 asdbd 在 a-Shell 内检查/修复：

```sh
python asdbd.py --client exec --cwd . -- \
  stat -f '%Su:%Sg %p %z %N' loader.py hermes.wasm hermes-runtime.zip
```

目标通常为：

```text
mobile:mobile
loader.py: 0755
hermes.wasm: 可读且可执行
```

设备端工具集有限，已知：

```text
sha256sum: command not found
ps: command not found
```

不要依赖这些命令。

## 8. asdbd 配置

`asdbd.py` 的上传限制已改为默认 512 MiB：

```python
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
```

`--client restart` 会移除旧的 `--max-bytes` 参数并强制加入：

```text
--max-bytes 536870912
```

这样 restart 不依赖旧服务启动时使用的限制。修改后曾通过本地编译检查、远端 update、restart 和 exec 探针验证。

推荐先确认控制通道：

```sh
python asdbd.py --client exec --cwd . -- pwd
python asdbd.py --client exec --cwd . -- python3 -c 'print("asdbd_alive")'
```

长时间 WebUI 进程必须使用明确的设备端日志和生命周期策略。短 timeout 只会断开主机客户端，不代表远程进程已退出；重复启动可能触发：

```text
Too many instances of this command are already running
```

## 9. 当前已知设备故障和最近日志

运行 WebUI 后曾出现：

```text
ConnectionResetError: [WinError 10054]
```

随后 asdbd 通道也可能不可用，表现为 `pwd`、`stat`、`exec` 全部无法连接。这表明 a-Shell/asdbd 进程级退出，不能简单归因于普通 Python traceback。

最近用户提供的设备截图显示，WebUI 进入了 urllib3/SSL 初始化路径。关键异常为：

```text
TypeError: encoding without a string argument
```

位置：

```text
ssl.py:set_alpn_protocols
```

随后再次运行时出现：

```text
AttributeError: 'SSLContext' object has no attribute 'post_handshake_auth'
```

位置：

```text
urllib3 -> http.client._create_https_context
```

这两项 facade 兼容问题已经在源代码中处理过，但设备上的 ZIP 可能仍是旧构建，或 ZIP 中存在重复 entry，必须重新生成无重复 entry 的 runtime 后再部署验证。

截图还表明用户是在 a-Shell 交互终端中运行：

```sh
./loader.py webui
```

并非此前的错误命令：

```sh
./loader.py >a.txt 2>&1
```

后者缺少 `webui` 参数，会进入默认聊天入口，并可能触发 `prompt_toolkit` 缺失路径，不能作为 WebUI 测试。

当前 iPad 曾完全无响应。设备恢复前不要反复运行 WebUI，也不要继续使用可能导致 a-Shell/asdbd 退出的测试。

## 10. iPad 恢复注意事项

无 Home 键 iPad 的强制重启：

1. 快速按下并释放靠近顶部键的音量键。
2. 快速按下并释放另一个音量键。
3. 立即长按顶部键。
4. 持续按住直到出现 Apple 标志，再松开。可能需要 10–30 秒。

有 Home 键的 iPad：同时长按 Home 键和顶部/电源键，直到出现 Apple 标志。

如果没有反应：接可靠电源等待 20–30 分钟，再重复强制重启。若仍无响应，进入恢复模式并优先选择电脑上的“更新”，不要未经确认选择“恢复”；恢复可能清除设备数据。

## 11. 已完成的主要修复提交

重要提交包括：

```text
261ce36 fix interactive stdin forwarding
222316f / 9d60439 socket facade and package fixes
587ddf1 WebUI integration
495a0d5 real zlib support
8892c1f / 82dfede / c679a62 / 33cef02 loader lifecycle and exit diagnostics
1aa730d raise asdbd upload limit to 512 MiB
565c848 force 512 MiB limit on asdbd restart
5748d0a verbose WebUI startup tracing
d2e06d2 allow selecting runtime archive on device
331e6e3 tolerate framed stderr in WebUI diagnostics
```

最近分支状态曾为：

```text
## default...origin/default
```

每次新的代码修复都应执行相关测试、`git diff --check`，并在交付前确认实际 commit SHA、远端同步状态和工作区状态。

## 12. 验证要求

必须区分三类证据：

1. 静态验证：WASM magic、ZIP integrity、entry 列表、符号和源码检查。
2. 主机侧验证：loader 单元测试、Python 编译检查、构建/打包脚本检查。
3. 真实设备验证：通过 a-Shell/asdbd 执行并捕获实际 stdout/stderr、设备端日志和退出码。

不能用静态探针代替真实 a-Shell 执行，也不能把 `pymobiledevice3` 成功上传文件当成 WebUI 启动成功。

历史本地测试结果：

```text
Ran 11 tests / OK
```

后续每次设备测试都应：

- 先确认 iPad 已恢复并且 asdbd 控制通道可用。
- 先执行最小 `asdbd_alive` 探针。
- 检查远端文件权限、大小和 runtime 路径。
- 使用固定 `hermes-runtime.zip`，避免启动早期找不到 `encodings`。
- 使用 bounded timeout 和明确日志文件。
- 测试失败后通过 `pymobiledevice3` 回读日志，再决定是否修改代码。
- 不要在没有新设备 traceback 的情况下猜测新的 Hermes 根因。
