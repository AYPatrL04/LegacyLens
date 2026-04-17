# Legacy Lens VSCode Extension

Legacy Lens 的 VSCode 扩展负责把编辑器中的悬停位置、选区、文件名和工作区信息发送给本地 Python 后端，并展示后端返回的解释。

## Motivation

VSCode hover 适合解释当前行，但它不能在返回后继续增量刷新。Legacy Lens 因此拆成两种交互：

- hover: 默认请求后端配置的 LLM provider，等待完整结果后一次性显示；如果需要更快响应，可以关闭 `legacyLens.hoverUseLlm`。
- `Analyze Selection`: 打开聊天式 Webview，调用 `/analyze/stream`，收到模型字符就立即显示。

这样可以同时满足“悬停时直接看到完整解释”和“需要实时增量输出时看流式 LLM 面板”。

## Usage

先编译扩展：

```powershell
cd vscode-extension
npm install
npm run compile
```

开发调试：

1. 在 VSCode 中打开仓库根目录或 `vscode-extension`。
2. 按 `F5` 启动 Extension Host。
3. 打开支持的代码文件，悬停在目标行上查看解释。
4. 选中一段代码后执行 `Legacy Lens: Analyze Selection`，查看流式分析面板。

扩展默认会尝试启动后端：

```text
python -m legacylens serve --host 127.0.0.1 --port 8765
```

当工作区根目录是本仓库时，扩展会把 `<workspace>/src` 加到 `PYTHONPATH`，所以开发时不需要先安装 Python 包。

## Commands

- `Legacy Lens: Analyze Selection`: 对当前选区或光标附近代码做流式分析。
- `Legacy Lens: Start Backend`: 启动本地后端。
- `Legacy Lens: Stop Backend`: 停止由扩展启动的本地后端。
- `Legacy Lens: Show Ollama Models`: 查询本地后端当前配置的 provider 和模型状态；本地模式列出 Ollama 模型，API 模式显示配置的 API 模型。

## Settings

- `legacyLens.backendUrl`: 后端地址，默认 `http://127.0.0.1:8765`。
- `legacyLens.maxContextLines`: hover 请求携带的代码窗口大小，默认 `80`。
- `legacyLens.useLlm`: `Analyze Selection` 和普通分析是否请求后端配置的 LLM provider，默认 `true`。
- `legacyLens.hoverUseLlm`: hover 是否直接等待 LLM 完整结果，默认 `true`。
- `legacyLens.contextScope`: 上下文范围，可选 `none`、`directory`、`project`，默认 `directory`。
- `legacyLens.autoStartBackend`: 后端不可用时是否自动启动，默认 `true`。
- `legacyLens.backendCommand`: 后端启动命令，默认 `python`。
- `legacyLens.backendArgs`: 后端启动参数。
- `legacyLens.backendCwd`: 后端工作目录；空值表示第一个 workspace folder。

## LLM Provider

扩展不直接保存 API key。LLM provider 由 Python 后端配置：

- 不提供配置文件时，后端继续使用本地 Ollama。
- 如果工作区根目录或上级目录存在 `.legacylens.local.json` 或 `.legacylens.json`，后端会读取其中的 `llm` 配置。
- 也可以通过 `LEGACYLENS_CONFIG` 指定配置文件路径。
- `llm.mode=api` 时，后端使用配置的 OpenAI-compatible Chat Completions API。
- `model` 是可选字段；本地模式省略时自动发现 Ollama 模型，API 模式省略时请求体不发送 `model`。

## Logging

扩展自动启动后端时，后端日志会进入 `Legacy Lens` Output Channel。日志包括 provider、model、host、调用成功/失败、fallback 原因和耗时。

后端不会把代码正文、prompt 正文或 API key 写入日志。可以通过 `LEGACYLENS_LOG_LEVEL` 调整日志级别。

## Privacy

默认配置下，扩展只和本机服务通信，后端再访问本机 Ollama：

```text
VSCode extension -> http://127.0.0.1:8765 -> local Ollama http://127.0.0.1:11434
```

隐私边界：

- 默认情况下，扩展不会把代码发送到云端 API；它只请求本机后端。
- 扩展没有遥测或远程上报逻辑。
- 发送给后端的数据包括当前代码窗口、文件名、工作区根目录和上下文配置，全部进入本地后端。
- 后端的目录扫描和 Ollama 调用也默认发生在本机。
- 如果用户手动把 `legacyLens.backendUrl` 配成远程地址，扩展会按该配置发送请求；这会改变默认隐私边界。
- 如果后端配置 `llm.mode=api`，后端会把 prompt 中的代码片段、文件名、目录上下文和静态命中发送到你配置的目标 API。

## Streaming UI

`Analyze Selection` 使用 NDJSON 流式接口：

- 收到 `metadata` 后显示语言、命中数量和模型状态。
- 收到每个 `delta` 后立即追加到回答区域。
- 收到 `fallback` 后提示为什么退回本地解释。
- 收到 `done` 后停止光标动画。

行号处理：

- 扩展会发送 `excerptStartLine` 和真实 `cursorLine`。
- 后端 prompt 使用真实文件行号，而不是片段相对行号。
- 如果模型提到没有证据支撑的行号，后端会追加行号校验提示。

## Supported Languages

扩展激活范围覆盖 C/C++、Fortran、COBOL、Assembly，以及 Python、Java、Go、C#、Rust、R、JavaScript/TypeScript、PHP、Ruby、Kotlin、Swift、Scala、SQL、shell、PowerShell、Lua、Perl、Haskell、Dart、Elixir、Erlang、Clojure、F#、Julia、HTML/CSS、Vue/Svelte、JSON/YAML/TOML/XML/Markdown、Dockerfile 等常见语言和项目文件。

## Development

编译：

```powershell
npm run compile
```

监听编译：

```powershell
npm run watch
```

不要提交：

- `node_modules/`
- `dist/`
- `*.vsix`

## License

Legacy Lens VSCode Extension 随主仓库一起以 Apache License 2.0 发布，完整文本见仓库根目录的 `LICENSE`，包元数据使用 SPDX 标识 `Apache-2.0`。
