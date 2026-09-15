# Hermes WASI 交付

本项目只定义两个运行时交付物：

- `hermes.wasm`：直接的 `wasm32-wasip1` 可执行文件；接受和输出格式化 frame
- `loader.py`：宿主 capability broker；转接格式化 stdin/stdout，并分类 stdio、stderr 和 socket

`asdbd.py` 是保留的 iPad 调试桥，不属于运行时交付物。无参数启动服务端，带 `--client` 启动客户端。

## hermes.wasm 标准输入输出协议

`hermes.wasm` 的标准输入和标准输出都是机器协议，不是终端文本流。WASM 内部
使用四字节大端长度加 JSON object 的 frame；`loader.py` 对外转接同一格式。
禁止在 stdout
写入 banner、日志、未编码 traceback 或其他普通文本。实现可以在构建目标上
使用内部二进制 transport，但对外交付协议必须保持下面的字段和语义。

请求：

```json
{"type":"request","id":1,"method":"stdio.write","params":{"stream":"stdin","data":{"encoding":"base64","data":"aGVsbG8K"}}}
```

socket 输入：

```json
{"type":"request","id":2,"method":"socket.connect","params":{"socket_id":1,"host":"example.com","port":443}}
{"type":"request","id":3,"method":"socket.send","params":{"socket_id":1,"data":{"encoding":"base64","data":"SGk="}}}
{"type":"request","id":4,"method":"socket.recv","params":{"socket_id":1,"size":4096}}
```

每个可接受请求都产生一个响应或事件。所有字节字段必须使用 base64：

```json
{"type":"response","id":4,"ok":true,"result":{"data":{"encoding":"base64","data":"SGVsbG8="}}}
```

stdio 输出、stderr 和 socket 输出只能通过事件返回：

```json
{"type":"event","event":"stdio.output","stream":"stdout","data":{"encoding":"base64","data":"UGVubg=="}}
{"type":"event","event":"stdio.output","stream":"stderr","data":{"encoding":"base64","data":"ZXJyb3I="}}
{"type":"event","event":"socket.data","socket_id":1,"data":{"encoding":"base64","data":"AQI="}}
```

输入不是合法 JSON、缺少必需字段、使用未知方法、base64 无效或超过大小限制时，
`hermes.wasm` 必须立即以非零状态退出，并把可读诊断写到 stderr；这些诊断不能进入
协议 stdout。`--version` 也必须走同一协议，不能直接输出裸版本字符串。

## overlay 和构建源码

上游源码不作为 submodule 提交。构建时执行：

```sh
./build/fetch-sources.sh
```

源码被固定提交下载到 `build/external/`：

- `build/external/hermes-agent/`
- `build/external/hermes-webui/`

本项目拥有的覆盖文件集中在：

- `overlay/python/`：CPython WASI 的 framed stdio、socket/TLS facade 和启动文件
- `overlay/hermes/`：Hermes Agent 的构建覆盖
- `overlay/webui/`：Hermes WebUI 的构建覆盖

构建时只把 overlay 复制到对应下载源码或 runtime staging 目录，不回写上游仓库。

## 本地构建

构建目标是 WSL Arch Linux 上的 `wasm32-wasip1`。构建使用 a-Shell 专用 WASI SDK（不是通用 WASI SDK）：

```text
https://github.com/holzschu/wasi-sdk.git
tag: wasi-sdk-aShell-22
commit: 3d2154daab00d4479d855a661355566b2e393702
```

构建脚本会自动获取并安装该 SDK。准备 CPython、
Wasmtime 和 SQLite 依赖后执行：

```sh
bash build/build-local-wasi.sh
```

构建脚本会先下载两个上游源码，再应用 overlay。最终 staging 时必须把
`overlay/python/` 复制到 CPython WASI 的 `lib/python3.13/` 目录，并生成项目根目录
的 `./hermes.wasm`。运行时标准库和 overlay 放在 `hermes-runtime/`，由 loader
通过 `HERMES_RUNTIME_ROOT` 或相邻目录发现；它们是 WASM 的运行时支持文件，不是
第三个入口交付物。

## 验收

```sh
python3 loader.py --version < version.frames > version.frames.out
python3 loader.py model < model.frames > model.frames.out
python3 loader.py < input.frames > output.frames
```

验收必须逐行解析 stdout，确认每一行都是协议 frame，并确认输出事件只属于
`stdio.output`、`socket.data` 或规定的响应类型。普通错误只检查 stderr；不能
把直接运行 `wasm`/`wasmtime` 的裸文本输出当作 `hermes.wasm` 协议验收。
