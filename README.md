# Hermes iOS Native Runtime

Hermes iOS 是面向越狱 iPad/iPhone arm64 的原生 Hermes Agent 运行时。它不是 a-Shell/WASI 交付路径，也不是使用 a-Shell 自带 Python 的外部 WebUI。

当前主分支：`default`

## 交付物

CI 会生成三个可分开下载的产物：

```text
hermes
hermesrt.zip
hermes-ios.deb
```

### `hermes`

静态链接 CPython 3.13、iOS 原生入口和必要的 native Python modules 的 arm64 Mach-O 可执行文件。

入口直接负责：

- 初始化静态 CPython
- 设置 ZIP runtime import paths
- 配置 stdout/stderr
- 启动 Hermes CLI、WebUI 和 `upgrade`

### `hermesrt.zip`

包含：

```text
hermes/          Hermes Agent Python 源码
hermes-webui/    Hermes WebUI Python 后端与静态资源
overlay/         当前 iOS 覆盖层与 patch
python/          CPython 标准库和纯 Python vendor 依赖
```

ZIP **不包含 `.git`**。升级时如果发现源码目录没有 `.git`，会在临时目录中使用纯 Python Git/archive 流程恢复 shallow source metadata；不会把 Git object database 交付到设备。

## 当前目录结构

```text
build/
├── build-hermesrt.sh         只构建 Python/Hermes runtime ZIP
├── build-native-hermes.sh    只构建 native Hermes Mach-O
├── build-native-ios.sh       兼容入口，依次调用以上两个脚本
├── package-hermesrt.sh       runtime ZIP 打包
├── package-native-hermes.sh  native Hermes 链接
├── package-native-ios-deb.sh rootless iOS Debian 安装包
└── fetch-sources.sh          固定上游 Agent/WebUI 源码

overlay/
├── cpython/Programs/
│   └── hermes_main.c          原生 CPython 入口
├── hermes/
│   ├── agent/
│   │   └── legacy_responses.py
│   └── hermes_cli/
│       ├── doctor_state.py
│       └── upgrade.py
├── python/
│   └── sitecustomize.py
└── patches/
    ├── patch-agent-sdk-compat.py
    ├── patch-ios-stability.py
    └── patch-webui-zip.py
```

`build/external/` 和 `/root/hermes-build/` 只用于构建期源码、SDK、缓存和中间产物，不应提交到 Git。

## 本地构建

要求：

- WSL/Linux
- Clang/LLD
- `make`
- `uv`
- Theos iPhoneOS SDK
- 网络访问 GitHub

构建使用 16 个并行任务。先构建目标 CPython 及其 iOS sysconfig 数据，再打包 runtime：

```sh
JOBS=16 bash build/build-native-hermes.sh
JOBS=16 bash build/build-hermesrt.sh
```

默认构建根目录：

```text
/root/hermes-build/native-ios
```

可以显式指定：

```sh
BUILD_ROOT=/root/hermes-build/native-ios \
IPHONEOS_DEPLOYMENT_TARGET=13.0 \
JOBS=16 bash build/build-native-hermes.sh
BUILD_ROOT=/root/hermes-build/native-ios \
JOBS=16 bash build/build-hermesrt.sh
```

仓库根目录没有可用的 Makefile；不要执行根目录 `make` 作为构建入口。

构建脚本会固定并准备：

- iPhoneOS SDK 16.5
- CPython 3.13.9
- OpenSSL 3.3.2
- 静态 `_ssl`、`_hashlib`、SQLite、Expat 和标准库模块
- 无 `.so` vendor native extensions

## WebUI 启动

设备上直接运行：

```sh
./hermes webui --host 127.0.0.1 --port 8787
```

默认验收地址：

```text
http://127.0.0.1:8787
```

启动输出必须包含：

```text
agent dir   : .../hermesrt.zip/hermes  [ok]
host:port   : 127.0.0.1:8787
Hermes Web UI listening on http://127.0.0.1:8787
```

最低真机检查：

```sh
curl -i http://127.0.0.1:8787/health
curl -i http://127.0.0.1:8787/
```

真实对话验收还必须完成：

1. `POST /api/session/new`
2. `POST /api/chat/start`
3. 读取 SSE stream
4. 收到 assistant 回复和终态事件
5. 对话期间 WebUI 进程持续存在
6. stderr 中没有 `Fatal Python error: Aborted`

## Runtime 升级

设备上执行：

```sh
./hermes upgrade
```

升级流程：

1. 将 `hermes/` 和 `hermes-webui/` 解压到临时目录
2. 如果存在 `.git`，先恢复 tracked 文件，再进行纯 Python fetch/update
3. 如果不存在 `.git`，下载上游 source archive 并创建最小 shallow metadata
4. 应用 `overlay/` 和打包 patch
5. 保留原 ZIP 的 `python/` 条目，不重建 Python runtime
6. 原子替换 `hermesrt.zip`

普通未越狱 iOS 的 CPython 默认禁用 `fork` 和 `subprocess.Popen`。越狱版 Hermes 检测到 Procursus Bash 与系统 `posix_spawn` 可用时，会为终端启用受限的 `Popen` spawn 路径：命令在 Bash 内切换到配置工作目录，子进程使用独立 session，并关闭未显式映射的非标准文件描述符。此支持不等同于完整 `fork`，不涵盖任意 `preexec_fn`、`pass_fds` 或通用 Python 多进程；其他模块如升级仍使用纯 Python Dulwich，避免依赖系统 `git` 子进程。

升级后必须重新验证：

```sh
./hermes webui --host 127.0.0.1 --port 8787
curl -i http://127.0.0.1:8787/health
curl -i http://127.0.0.1:8787/
```

## GitHub Actions

workflow：

```text
.github/workflows/build-native-ios.yml
```

触发方式：

- push 到 `default`
- GitHub Actions 页面手动执行 `workflow_dispatch`

成功后上传三个 Actions artifacts：

```text
hermes-native-ios
hermesrt-native-ios
hermes-deb-native-ios
```

每次构建成功后，workflow 会自动将三个文件上传到固定 Release
[`v2-native`](https://github.com/cyanmint/hermes-ios/releases/tag/v2-native)，并用本次产物覆盖同名旧文件：

`hermes-ios.deb` 是面向 rootless 越狱环境的 `iphoneos-arm64` 包，要求 iOS 13 或更新版本。包内将二进制与 runtime 安装到 `/var/jb/usr/libexec/hermes-ios/`，并安装 `/var/jb/usr/bin/hermes` 启动器以设置 runtime 路径。

workflow 构建门禁会检查：

- Mach-O 和 ZIP 存在，Mach-O 已完成 ad-hoc 代码签名
- ZIP integrity
- 必需 runtime paths，包括目标 iOS sysconfig 数据
- Codex Responses SSE 兼容层保留文本与工具调用事件
- 禁止 `.so`、`.dylib`、`.pyd`、`.wasm`
- 禁止嵌入 `.git`

## 越狱设备安装

从上述 Release 下载 `hermes-ios.deb`，通过 Sileo/Zebra 等包管理器打开并安装；也可复制到设备后使用 Procursus `dpkg -i ./hermes-ios.deb` 安装。安装完成后可直接运行：

```sh
hermes --version
hermes webui --host 127.0.0.1 --port 8787
```

包内 Hermes 已由 CI 使用 `ldid` ad-hoc 签名，且包管理器会按 `/var/jb` rootless 布局安装文件。

## 手动真机部署

GitHub Actions artifact 已完成 ad-hoc 签名。若使用本地构建的未签名可执行文件，先在设备上执行 `ldid -S ./hermes`；否则 iOS 会直接终止它。

通过 USB SSH 转发建立连接：

```sh
iproxy 2222 22
```

部署两个交付物：

```sh
scp -P 2222 hermes hermesrt.zip root@127.0.0.1:/var/jb/var/root/
```

然后在设备上：

```sh
chmod 755 ./hermes
./hermes --version
./hermes webui --host 127.0.0.1 --port 8787
```

设备验证不能由主机导入、`doctor` 或静态 ZIP 检查替代。必须记录真实退出码、HTTP 状态、响应内容、SSE 事件、进程状态和 stderr。

## 设计边界

- `core/native` 能力只负责 CPython 宿主、socket/TLS、存档和通用运行时
- Agent 与 WebUI Python 源码属于 runtime overlay/source payload
- Python 依赖在构建期固定，设备上不执行 `pip install` 或 `uv pip install`
- 不把 API key、token、密码或其他凭据写入 artifact、日志或 workflow 输出
- `python/` 在 `upgrade` 中保持不变；Agent/WebUI 更新通过 overlay 和源码更新完成
- 纯静态检查不能代替真实越狱 iOS 验收
