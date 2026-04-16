# Legacy Lens

Legacy Lens 是一个本地运行的代码解释工具。它面向两类场景：

- 维护 COBOL、Fortran、C/C++、汇编等遗留代码时，解释那些依赖标签跳转、共享存储、位压缩或固定记录布局的写法。
- 阅读现代项目时，在悬停行附近给出这段代码的实际行为，以及它在当前目录或项目上下文中的作用。

它不是“把代码翻译成另一门语言”的工具。更准确地说，它把静态命中、目录上下文和本地 Ollama 模型结合起来，给出可验证的代码阅读辅助。

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
  -> optional local Ollama, default http://127.0.0.1:11434
```

主要模块：

- `src/legacylens/server.py`: 本地 HTTP 服务，提供 `/health`、`/models`、`/analyze`、`/analyze/stream`。
- `src/legacylens/engine.py`: 语言识别、analyzer 调度、上下文扫描、解释生成入口。
- `src/legacylens/analyzers/`: 静态规则。遗留语言使用专用 analyzer，现代语言使用 profile-based mainstream analyzer。
- `src/legacylens/context.py`: 当前目录或项目范围内的文件和符号引用扫描。
- `src/legacylens/llm.py`: 本地 Ollama 调用、流式输出、行号校验。
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

列出本地 Ollama 模型：

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

使用本地 Ollama：

```powershell
$env:PYTHONPATH = "src"
@'
int flags = 0;
flags = flags | 001;
'@ | python -m legacylens analyze - --language c --cursor-line 2 --context-scope none --use-llm
```

可选环境变量：

- `OLLAMA_HOST`: Ollama 地址，默认 `http://127.0.0.1:11434`。
- `LEGACYLENS_OLLAMA_MODEL`: 指定模型名。
- `LEGACYLENS_OLLAMA_PREFER`: 自动选择模型时的偏好关键词，默认优先 `qwen`、`deepseek`、`codellama` 等。
- `LEGACYLENS_OLLAMA_TIMEOUT`: 生成超时时间，默认 60 秒。
- `LEGACYLENS_DISABLE_OLLAMA_AUTODISCOVERY`: 设为 `true` 后不自动扫描本地 Ollama 模型。

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
- `Legacy Lens: Show Ollama Models`: 查看后端能看到的本地 Ollama 模型。

Hover 行为：

- hover 默认直接请求 LLM，`legacyLens.hoverUseLlm` 默认是 `true`。
- VSCode hover 无法边生成边刷新，因此 hover 会等待完整 LLM 结果后一次性显示。
- `Legacy Lens: Analyze Selection` 用流式 Webview 展示 LLM 输出，收到字符就追加显示。
- 如果希望 hover 更快返回本地静态解释，可以把 `legacyLens.hoverUseLlm` 设为 `false`。

主要设置：

- `legacyLens.backendUrl`: 本地后端地址，默认 `http://127.0.0.1:8765`。
- `legacyLens.maxContextLines`: hover 附近发送给后端的最大行数。
- `legacyLens.useLlm`: 是否请求 Ollama 解释，默认 `true`。
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
- LLM 调用只发往本地 Ollama，默认是 `http://127.0.0.1:11434`。
- 项目代码、目录结构、hover 片段和模型输出不会发送到外部 API。
- 扩展没有遥测逻辑，也没有远程数据上报逻辑。

边界说明：

- `npm install`、未来可能的 Python 依赖安装属于开发环境准备，可能访问包管理源；这不属于运行时代码分析路径。
- 如果你主动把 `legacyLens.backendUrl` 或 `OLLAMA_HOST` 配置成远程地址，代码片段会发送到你配置的远程服务。默认配置不这样做。

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
