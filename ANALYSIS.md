# 架构与边界

## 交付边界

本专案不把 `hermes-agent` 或 `hermes-webui` 当作 Git submodule。它们是构建输入，
由 `build/fetch-sources.sh` 下载到 `build/external/`，再接受本专案 `overlay/`
覆盖。这样提交中只保留可审查的变更，不把上游完整工作树嵌入专案。

最终运行时交付物只有：

1. `hermes.wasm`：直接的 WASM runtime，接受格式化输入并输出格式化 frame
2. `loader.py`：宿主 capability broker，转接 frame 并分类 stdio、stderr、socket

`ashell-debug-*` 仅负责通过 a-Shell 验证，不改变交付协议，也不替代协议测试。

## 协议边界

stdin 是唯一输入通道，只接受 UTF-8 JSON frame。所有 socket 字节和 stdio 字节
都必须包装为 `{encoding: "base64", data: "..."}`。stdout 是唯一机器输出通道，
只允许 JSON frame，输出分为：

- `response`：对应带 `id` 的请求
- `stdio.output`：应用 stdout 或 stderr 的编码字节
- `socket.data`：socket 接收数据的编码字节

普通诊断、协议错误和启动错误写入进程 stderr。stderr 不能混入 stdout frame，
stdout 也不能混入普通日志。`--version`、空参数启动和 `model` 命令使用完全相同
的 framing 规则。

## overlay 规则

WASI 编译器必须来自 a-Shell 定制 SDK，而不是 generic wasi-sdk：
`https://github.com/holzschu/wasi-sdk.git` 的 `wasi-sdk-aShell-22`
（commit `3d2154daab00d4479d855a661355566b2e393702`）。
`build/fetch-ashell-wasi-sdk.sh` 负责递归下载、校验和安装该工具链。

`overlay/python/` 是 WASI runtime 的覆盖层；它不依赖直接修改 CPython checkout。
`overlay/hermes/` 和 `overlay/webui/` 分别覆盖 build-time 下载的上游源码。复制
操作必须在构建阶段执行，目标文件不存在时应失败，避免拼写错误导致静默漏拷贝。

构建脚本必须记录实际使用的上游 commit 到 `build/external/SOURCES`。修改上游版本
时应显式更新 commit 环境变量或脚本默认值，并重新运行协议、语法和 WASM import
检查。

## 不属于当前目标的内容

当前不以裸 `wasm`/`wasmtime` 的终端文本输出作为 `hermes.wasm` 协议验收。loader
负责把外部格式化输入送入 WASM、把 WASM 的格式化事件转发给外部，并代理 socket
与 TLS capability；普通诊断始终走 stderr。
