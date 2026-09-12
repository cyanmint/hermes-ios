# a-Shell package: Hermes WebUI + Agent

这是一个可在 a-Shell 上尝试的打包版本，包含 Python WebUI、Hermes Agent 和用于配置 provider 的 CLI，不包含 TUI、Node.js/WASM 或 React/Vite WebUI。

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

如果检查显示缺少 `dotenv`，运行：

```sh
sh a-shell-install-deps.sh
```

FastAPI 和 uvicorn 不需要安装：当前使用的独立 WebUI 采用自带的标准库 HTTP server。不要在 a-Shell 中执行完整 Hermes Agent 的依赖安装，因为其中包含无法在 iOS 上构建的原生扩展。

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

## 使用 Hermes CLI 配置 provider

请使用包内 CLI；它只负责设置 a-Shell 运行环境，所有命令、选项和参数都会原样转发给上游 `hermes_cli.main`。

```sh
sh a-shell-cli.sh --help
sh a-shell-cli.sh config path
sh a-shell-cli.sh config set model.provider openrouter
sh a-shell-cli.sh config set model.default anthropic/claude-sonnet-4
sh a-shell-cli.sh setup model
sh a-shell-cli.sh model
```

`hermes model` 是上游的交互式模型配置入口。选择 `copilot` provider 时，如果没有现有凭据，上游会启动 GitHub Copilot OAuth device-code 登录：终端会显示 GitHub 验证网址和一次性代码，在 Safari 完成授权后返回 a-Shell，凭据由上游保存到包内 Hermes 状态目录。不要把显示的代码或 token 发给任何人。

也可以直接使用上游的其他命令，例如：

```sh
sh a-shell-cli.sh auth --help
sh a-shell-cli.sh config --help
sh a-shell-cli.sh doctor --help
sh a-shell-cli.sh gateway --help
```

API key 仍然是 secret，应写入此包目录的 `.env`，不要写入 `config.yaml`：

```sh
printf '%s\n' 'OPENROUTER_API_KEY=[REDACTED]' >> .env
```

也可以在启动 CLI 前临时导出环境变量。`a-shell-cli.sh` 与 WebUI 使用同一个包目录状态，所以配置会立即共享。

如果仍然出现 workspace 错误，可以手动执行：

```sh
mkdir -p "$PWD/workspace"
export HERMES_WEBUI_DEFAULT_WORKSPACE="$PWD/workspace"
sh a-shell-start.sh
```

当前包会将 Hermes 状态、配置和 WebUI 数据保存到压缩包目录本身（`HERMES_HOME=.`），将 workspace 也使用压缩包目录。这样可以避开 a-Shell 不可写的 `$HOME` 根目录。
```sh
export HERMES_HOME=.
export HERMES_WEBUI_PORT=8787
```

启动脚本会自动覆盖 a-Shell 继承的不可写 `HERMES_HOME`。如需使用其他可写目录，请设置 `HERMES_A_SHELL_HOME`，不要设置 `HERMES_HOME`：

```sh
export HERMES_A_SHELL_HOME=./profile
```

模型/API 配置沿用 Hermes Agent 的配置目录。不要把 API key 写入此压缩包或 Git。

## 当前限制

- 启动脚本不会自动运行官方 Hermes 安装器
- CLI 参数和子命令会原样转发；具体命令是否可运行取决于 a-Shell 中已安装的依赖和网络能力，缺失可选依赖时上游会返回原始错误
- `hermes model` 支持通过上游 GitHub Copilot device-code OAuth 流程登录；OAuth 需要 iPad 能访问 GitHub
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
