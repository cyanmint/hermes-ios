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

`native/hermes_main.c` 是唯一原生入口：它把用户参数转交给
`hermes_cli.main`，并把相邻的 `hermesrt.zip` 设置为 Python import path。

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
