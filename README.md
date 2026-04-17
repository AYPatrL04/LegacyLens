# Legacy Lens

Legacy Lens 是一个本地运行的代码解释工具。它面向两类场景：

- 维护 COBOL、Fortran、C/C++、汇编等遗留代码时，解释那些依赖标签跳转、共享存储、位压缩或固定记录布局的写法。
- 阅读现代项目时，在悬停行附近给出这段代码的实际行为，以及它在当前目录或项目上下文中的作用。

它不是“把代码翻译成另一门语言”的工具。更准确地说，它把静态命中、目录上下文和配置的 LLM provider 结合起来，给出可验证的代码阅读辅助；默认 provider 是本地 Ollama。

## Motivation

很多老系统的问题不在语法本身，而在隐含约束：

- Fortran 的 `COMMON` 和 `EQUIVALENCE` 把数据布局变成了隐式契约。
- COBOL 的 `PERFORM THRU`、`REDEFINES` 和句点作用域会让控制流、数据布局依赖源码顺序和固定宽度记录。
- C/C++ 和汇编中的 bit mask、union、宏和跳转往往是性能、内存或设备协议约束下的产物。
- 现代项目中，单行 `open()`、`fetch()`、`defer`、`?`、`await`、LINQ 或管道操作，也需要结合当前目录和引用关系判断影响面。

Legacy Lens 的目标是让悬停解释尽量集中在具体代码上：它做什么、它在本目录或项目里可能承担什么职责、哪些行号和引用能支撑这个判断、下一步该看哪里。

## Architecture

运行路径默认全部在本机：

```text
VSCode extension or CLI
  -> local Python backend, default http://127.0.0.1:8765
  -> static analyzers and local context scanner
  -> configured LLM provider
     -> default local Ollama, http://127.0.0.1:11434
     -> optional OpenAI-compatible API
```

主要模块：

- `src/legacylens/server.py`: 本地 HTTP 服务，提供 `/health`、`/models`、`/analyze`、`/analyze/stream`。
- `src/legacylens/engine.py`: 语言识别、analyzer 调度、上下文扫描、解释生成入口。
- `src/legacylens/analyzers/`: 静态规则。遗留语言使用专用 analyzer，现代语言使用 profile-based mainstream analyzer。
- `src/legacylens/context.py`: 当前目录或项目范围内的文件和符号引用扫描。
- `src/legacylens/llm.py`: LLM provider 选择、本地 Ollama/API 调用、流式输出、行号校验。
- `vscode-extension/`: VSCode 前端，支持 hover 和聊天式流式分析面板。

Analyzer 分工：

- `CLikeAnalyzer`: C/C++ 的 goto、union、bit packing、宏、八进制字面量。
- `FortranAnalyzer`: COMMON、EQUIVALENCE、computed GOTO、arithmetic IF、label DO。
- `CobolAnalyzer`: PERFORM THRU、ALTER、GO TO、REDEFINES、OCCURS DEPENDING ON、NEXT SENTENCE。
- `AssemblyAnalyzer`: 跳转、位移/旋转、原始字节。
- `MainstreamAnalyzer`: Python、Java、Go、C#、Rust、R、JavaScript/TypeScript、SQL、shell、配置文件等现代语言 profile。规则 ID 使用语言前缀，例如 `python.file-io`、`go.goroutine`、`rust.result-propagation`。

## Usage

### Backend

从仓库根目录启动本地服务：

```powershell
$env:PYTHONPATH = "src"
python -m legacylens serve --host 127.0.0.1 --port 8765
```

检查服务状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

查看当前 LLM provider 和模型状态：

```powershell
$env:PYTHONPATH = "src"
python -m legacylens models
```

不使用 LLM，只运行静态分析和本地 fallback 解释：

```powershell
$env:PYTHONPATH = "src"
@'
      COMMON /A/ X, Y
      GO TO 100
'@ | python -m legacylens analyze - --language fortran --cursor-line 1
```

使用配置的 LLM provider：

```powershell
$env:PYTHONPATH = "src"
@'
int flags = 0;
flags = flags | 001;
'@ | python -m legacylens analyze - --language c --cursor-line 2 --context-scope none --use-llm
```

可选环境变量：

- `LEGACYLENS_CONFIG`: 显式指定配置文件路径。
- `LEGACYLENS_LOG_LEVEL`: 后端日志级别，默认 `INFO`；可设为 `DEBUG`、`WARNING` 等。
- `LEGACYLENS_LLM_MODE`: `local` 或 `api`，默认 `local`。
- `LEGACYLENS_MODEL`: 通用模型名；可省略。
- `OLLAMA_HOST`: Ollama 地址，默认 `http://127.0.0.1:11434`。
- `LEGACYLENS_OLLAMA_MODEL`: 指定模型名。
- `LEGACYLENS_OLLAMA_PREFER`: 自动选择模型时的偏好关键词，默认优先 `qwen`、`deepseek`、`codellama` 等。
- `LEGACYLENS_OLLAMA_TIMEOUT`: 生成超时时间，默认 60 秒。
- `LEGACYLENS_DISABLE_OLLAMA_AUTODISCOVERY`: 设为 `true` 后不自动扫描本地 Ollama 模型。
- `LEGACYLENS_API_URL`: 完整 Chat Completions API 地址。
- `LEGACYLENS_API_BASE_URL`: API base URL；会和 `LEGACYLENS_API_PATH` 拼接。
- `LEGACYLENS_API_PATH`: API 路径，默认 `/chat/completions`。
- `LEGACYLENS_API_KEY`: API key。
- `LEGACYLENS_API_KEY_ENV`: 从指定环境变量读取 API key。
- `LEGACYLENS_API_MODEL`: API 模型名；可省略，省略时请求体不发送 `model` 字段。

### LLM Configuration

如果不提供配置文件，Legacy Lens 维持当前本地 Ollama 用法：自动访问 `http://127.0.0.1:11434` 并尝试从 `/api/tags` 选择本地模型。

配置文件查找顺序：

1. `LEGACYLENS_CONFIG` 指定的路径。
2. 从当前工作目录向上查找 `.legacylens.local.json`。
3. 从当前工作目录向上查找 `.legacylens.json`。

这两个本地配置文件默认被 `.gitignore` 排除，避免误提交 API key。

本地模式，`model` 可省略；省略时自动发现 Ollama 模型：

```json
{
  "llm": {
    "mode": "local",
    "local": {
      "host": "http://127.0.0.1:11434",
      "model": "qwen3.5:9b",
      "prefer": ["qwen", "deepseek"],
      "disableAutodiscovery": false
    }
  }
}
```

### Logging

后端默认输出 INFO 级别日志。日志包含：

- LLM provider、model、host。
- 调用开始、成功、失败。
- stream/non-stream 标记。
- prompt 字符数、输出字符数、耗时。
- fallback 原因。
- 行号校验警告。

日志不会记录代码正文、prompt 正文或 API key。若 URL 查询参数中包含 `api_key`、`key` 或 `token`，日志会做脱敏。

调整日志级别：

```powershell
$env:LEGACYLENS_LOG_LEVEL = "DEBUG"
```

API 模式使用 OpenAI-compatible Chat Completions 请求。`model` 同样可省略；省略时请求体不发送 `model` 字段，由目标 API 自行决定默认模型：

```json
{
  "llm": {
    "mode": "api",
    "api": {
      "baseUrl": "https://api.example.com/v1",
      "path": "/chat/completions",
      "apiKeyEnv": "LEGACYLENS_REMOTE_API_KEY",
      "model": "optional-model-name",
      "headers": {
        "X-Provider": "example"
      }
    }
  }
}
```

也可以直接写完整 URL：

```json
{
  "llm": {
    "mode": "api",
    "api": {
      "url": "https://api.example.com/v1/chat/completions",
      "apiKey": "not-recommended-in-repo",
      "model": ""
    }
  }
}
```

### VSCode Extension

安装前端依赖并编译：

```powershell
cd vscode-extension
npm install
npm run compile
```

开发调试时，在 VSCode 中打开仓库或 `vscode-extension`，按 `F5` 启动 Extension Host。

常用命令：

- `Legacy Lens: Analyze Selection`: 打开聊天式 Webview，并通过 `/analyze/stream` 一边生成一边显示。
- `Legacy Lens: Start Backend`: 启动本地 Python 后端。
- `Legacy Lens: Stop Backend`: 停止由扩展启动的后端进程。
- `Legacy Lens: Show Ollama Models`: 查看后端当前配置的 provider 和模型状态；本地模式列出 Ollama 模型，API 模式显示配置的 API 模型。

Hover 行为：

- hover 默认直接请求 LLM，`legacyLens.hoverUseLlm` 默认是 `true`。
- VSCode hover 无法边生成边刷新，因此 hover 会等待完整 LLM 结果后一次性显示。
- `Legacy Lens: Analyze Selection` 用流式 Webview 展示 LLM 输出，收到字符就追加显示。
- 如果希望 hover 更快返回本地静态解释，可以把 `legacyLens.hoverUseLlm` 设为 `false`。

主要设置：

- `legacyLens.backendUrl`: 本地后端地址，默认 `http://127.0.0.1:8765`。
- `legacyLens.maxContextLines`: hover 附近发送给后端的最大行数。
- `legacyLens.useLlm`: 是否请求配置的 LLM provider，默认 `true`。
- `legacyLens.hoverUseLlm`: hover 是否直接用 LLM，默认 `true`。
- `legacyLens.contextScope`: `none`、`directory` 或 `project`，默认 `directory`。
- `legacyLens.autoStartBackend`: 首次使用时是否自动启动后端，默认 `true`。
- `legacyLens.backendCommand`、`legacyLens.backendArgs`、`legacyLens.backendCwd`: 后端启动命令、参数和工作目录。

### Streaming API

`POST /analyze/stream` 返回 NDJSON，每行一个事件：

- `metadata`: 静态命中、上下文、模型状态。
- `delta`: 一段新生成文本，客户端应立即追加显示。
- `fallback`: 本地 fallback 原因。
- `done`: 结束事件。

请求模板：

```powershell
$body = @{
  code = "def load(path):`n    return open(path).read()`n"
  fileName = "inline.py"
  language = "python"
  cursorLine = 2
  contextScope = "none"
  useLlm = $true
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri http://127.0.0.1:8765/analyze/stream `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## Supported Languages

遗留语言和系统语言：

- C/C++/Objective-C: `.c`、`.h`、`.cc`、`.cpp`、`.cxx`、`.hpp`、`.m`、`.mm`
- Fortran: `.f`、`.f77`、`.for`、`.ftn`
- COBOL: `.cob`、`.cbl`、`.cpy`
- Assembly: `.asm`、`.s`

现代主流语言：

- Python、Java、Go、C#、Rust、R
- JavaScript、TypeScript、PHP、Ruby、Kotlin、Swift、Scala、SQL
- shell、PowerShell、Batch、Lua、Perl、Haskell、Dart、Elixir、Erlang、Clojure、F#、Julia、Visual Basic、Groovy
- HTML、CSS、SCSS、Sass、Less、Vue、Svelte
- JSON、YAML、TOML、XML、Markdown、Dockerfile

## Privacy

Legacy Lens 的默认运行路径完全在本地：

- VSCode 扩展只请求 `legacyLens.backendUrl`，默认是 `http://127.0.0.1:8765`。
- Python 后端只在本机分析代码、扫描本地目录、读取本地事实库。
- 默认 LLM 调用只发往本地 Ollama，默认是 `http://127.0.0.1:11434`。
- 默认情况下，项目代码、目录结构、hover 片段和模型输出不会发送到外部 API。
- 扩展没有遥测逻辑，也没有远程数据上报逻辑。
- 后端日志只记录调用元数据，不记录代码正文、prompt 正文或 API key。

边界说明：

- `npm install`、未来可能的 Python 依赖安装属于开发环境准备，可能访问包管理源；这不属于运行时代码分析路径。
- 如果你主动把 `legacyLens.backendUrl` 或 `OLLAMA_HOST` 配置成远程地址，代码片段会发送到你配置的远程服务。默认配置不这样做。
- 如果你把 `llm.mode` 或 `LEGACYLENS_LLM_MODE` 设为 `api`，后端会把 prompt 中的代码片段、文件名、目录上下文和静态命中发送到你配置的目标 API。

## Development

运行测试：

```powershell
python -m unittest discover -s tests -v
```

编译 VSCode 扩展：

```powershell
cd vscode-extension
npm run compile
```

建议不要提交以下内容：

- `.venv/`
- `vscode-extension/node_modules/`
- `vscode-extension/dist/`
- IDE 本地配置，例如 `.idea/`，除非项目明确决定托管它们

## License

Legacy Lens 适合使用 Apache License 2.0，原因是当前仓库没有已有许可证冲突，Python 主包没有运行时依赖，VSCode 扩展的开发依赖主要是 MIT 或 Apache-2.0 许可；Apache-2.0 同时保留了宽松再分发、修改和商业使用空间，并提供明确的专利授权条款。

本项目以 Apache License 2.0 发布，完整文本见仓库根目录的 `LICENSE`。包元数据使用标准 SPDX 标识 `Apache-2.0`。

再分发或修改时请保留 `LICENSE`，保留源码中已有的版权、专利、商标和归属声明，并在修改过的文件中清楚标明变更。当前仓库没有单独的 `NOTICE` 文件；如果未来加入 `NOTICE`，分发衍生作品时也需要按 Apache-2.0 要求保留其中适用的归属说明。
