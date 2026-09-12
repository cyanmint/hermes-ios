# a-Shell package: Hermes WebUI + Agent

这是第一个可在 a-Shell 上尝试的打包版本。当前只包含 Python WebUI 和 Hermes Agent，不包含 TUI、Node.js/WASM 或 React/Vite WebUI。

## 安装

在 a-Shell 中解压此压缩包，然后进入目录：

```sh
unzip hermes-ios-webui-alpha.zip
cd hermes-ios-webui-alpha
```

先运行依赖/导入检查：

```sh
python3 a-shell-check.py
```

如果检查显示缺少 `fastapi`、`uvicorn`、`openai`、`pydantic` 或 `python-dotenv`，运行：

```sh
sh a-shell-install-deps.sh
```

然后再次运行检查：

```sh
python3 a-shell-check.py
```

如果 a-Shell 的 Python 环境已经提供所需依赖，可以直接启动：

```sh
sh a-shell-start.sh
```

然后在 iPad Safari 打开：

```text
http://127.0.0.1:8787
```

## 配置

首次使用前，可以在 a-Shell 中设置：

```sh
export HERMES_HOME="$HOME/.hermes"
export HERMES_WEBUI_PORT=8787
```

模型/API 配置沿用 Hermes Agent 的配置目录。不要把 API key 写入此压缩包或 Git。

## 当前限制

- 启动脚本不会自动运行官方 Hermes 安装器
- 启动脚本不会创建虚拟环境
- 不会自动安装依赖
- Agent 依赖是否能在 a-Shell Python 中导入，需要以 `a-shell-check.py` 的实际结果为准
- 当前只绑定 `127.0.0.1`，只能从同一台 iPad 的浏览器访问
- TUI、Node/WASM、MCP、浏览器、Gateway、语音和媒体功能暂未纳入本包

## 故障报告

请把以下命令的完整输出发回：

```sh
python3 a-shell-check.py
```

如果检查通过但启动失败，请发回：

```sh
sh a-shell-start.sh
```

以及完整错误信息。
