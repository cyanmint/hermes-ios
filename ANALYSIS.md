# Hermes iOS / a-Shell 初步分析

## 当前来源

本仓库通过 Git submodule 固定了两个上游来源：

- `hermes-agent` → `NousResearch/hermes-agent`
- `hermes-webui` → `nesquena/hermes-webui`

两者不是同一个 WebUI：Hermes Agent 自身也包含一个 `web/` React/Vite dashboard；外部 `hermes-webui` 是独立的 Python + vanilla JavaScript WebUI。当前工作目标使用外部 `hermes-webui`。

## 当前固定版本

- Hermes Agent: `53c57871d67ee7d2202861aacc4ea0ef6ef93112`
- Hermes WebUI: `b1286878a437d374d2bfb6db3e3d084e33d3fe5d`
- 根仓库初始提交: `4e636b4`

两个 submodule 当前均为浅克隆，根仓库记录的是明确的 gitlink 提交。

## WebUI 运行模型

外部 WebUI 不需要 Node、Vite、npm 或前端构建：

```text
Python WebUI server
├── static/ vanilla JavaScript
├── API routes
└── in-process Hermes Agent
```

`hermes-webui/api/config.py` 支持通过 `HERMES_WEBUI_AGENT_DIR` 指向 Agent 源码目录，并将该目录加入 Python import 路径。WebUI 的 chat 默认在进程内导入 `run_agent.AIAgent`，不是通过 OpenAI-compatible API 连接另一个 Agent。

因此 a-Shell 第一阶段应优先运行独立 WebUI 的 Python server，并将：

```text
HERMES_WEBUI_AGENT_DIR=<本地>/hermes-agent
```

指向 Agent submodule。

## 依赖现状

外部 WebUI 的 `requirements.txt` 只有基础 WebUI 依赖：

- `pyyaml`
- `cryptography`
- `edge-tts` 可选
- `psutil` 可选
- Office 文档解析器可选

但 WebUI 会直接使用 Hermes Agent 的 Python 依赖。Agent 当前 `pyproject.toml` 的基础依赖仍包含大量与 iOS 无关或可能不兼容的包，并且包含 FastAPI、uvicorn、python-multipart 等 dashboard 依赖。不能只安装 WebUI 的 `requirements.txt` 后就认为 Agent 可运行；需要在 a-Shell Python 环境中逐项进行 import/startup 验证。

## 第一阶段边界

暂不处理：

- `ui-tui`
- Node.js/WASM
- Electron/Desktop
- Hermes Agent 内置 React/Vite WebUI
- Gateway、MCP、浏览器、语音和媒体扩展

先验证：

```text
a-Shell Python
→ external hermes-webui server
→ static WebUI via iOS browser
→ Agent import
→ 配置加载
→ 模型请求
→ 基础对话
→ 会话保存/恢复
```

## 主要风险

1. Hermes Agent 的 `run_agent.py` 及其导入链可能触发 a-Shell 不支持的依赖。
2. WebUI bootstrap 默认会创建/寻找独立虚拟环境，并可能尝试调用官方安装器；iOS 发行版不应直接使用该自动安装路径。
3. WebUI 当前会将 Agent 源码加入 `sys.path` 并进行进程内导入，因此 Agent 与 WebUI 必须使用同一个兼容的 Python 解释器和明确的 `HERMES_HOME`。
4. 外部 WebUI 与 Agent 内置 `web/` 的 API 契约可能不同，不能混用静态资源或启动命令。

下一步应先编写不安装依赖的静态 import/启动探针，识别 a-Shell 基础 Python 能否加载 `hermes-webui` 和 `run_agent.AIAgent`，然后再建立 iOS 专用依赖清单与启动脚本。
