# Hermes 原生 iOS 交付

本项目定义两个越狱 iOS（arm64）运行时交付物：

- `./hermes`：静态链接 CPython 与所需标准库 native modules 的原生 Mach-O 可执行文件
- `hermesrt.zip`：完整 Python 标准库、Hermes Agent 和 WebUI 运行时

`./hermes` 直接启动 `hermes_cli.main`，使用 iOS 原生 socket/TLS 能力。

## 构建

构建必须在 WSL/Linux 执行，并下载 Theos iPhoneOS SDK 和 CPython 源码：

```sh
IPHONEOS_DEPLOYMENT_TARGET=13.0 bash build/build-native-ios.sh
```

脚本输出 `./hermes` 和 `./hermesrt.zip`。SDK、CPython 和中间产物放在
`BUILD_ROOT`（默认 `/root/hermes-build/native-ios`），不会写入 Git 工作树。

## 源码和运行时

上游源码不作为 submodule 提交。构建时执行：

```sh
./build/fetch-sources.sh
```

源码被固定提交下载到 `build/external/`：

- `build/external/hermes-agent/`
- `build/external/hermes-webui/`

`overlay/` 是受版本控制的 iOS 覆盖层，按最终 runtime 目录组织：

- `overlay/cpython/Programs/hermes_main.c`
- `overlay/hermes/`
- `overlay/python/`
- `overlay/patches/`

它们在打包阶段覆盖/补丁到 `hermesrt.zip` staging；`build/external/` 只保存可重建的上游临时源码。

runtime ZIP 同时保留两个上游 shallow clone：

```text
hermes/.git/
hermes-webui/.git/
overlay/
```

真机执行 `./hermes upgrade` 时，会在临时目录中先对两个 clone 执行
`git reset --hard HEAD`、`git clean -fd`，再执行 `git pull --ff-only`，重新应用
`overlay/` 和 patch，并原子重写 `hermesrt.zip`。`python/` 条目会从旧 ZIP 原样复制，
不会随 Agent/WebUI 更新而重建。

## 验收

主机侧只能检查 Mach-O 结构和 ZIP 内容：

```sh
file ./hermes
unzip -t hermesrt.zip
```

设备侧直接执行：

```sh
./hermes --version
./hermes doctor
```

越狱 iOS 验收还必须确认 `hermesrt.zip` 可读，原生 `_socket`、`ssl`、`sqlite3`
模组可导入，以及 WebUI 能够绑定端口。Windows 或 WSL 不能代替设备执行验证。
