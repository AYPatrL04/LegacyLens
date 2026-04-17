# Legacy Lens

语言：[English](README.md) | [简体中文](README.zh-CN.md)

Legacy Lens 是一个本地运行的代码解释工具。它面向两类场景：

- 维护 COBOL、Fortran、C/C++、汇编等遗留代码时，解释那些依赖标签跳转、共享存储、位压缩或固定记录布局的写法。
- 阅读现代项目时，在悬停行附近给出这段代码的实际行为，以及它在当前目录或项目上下文中的作用。

它不是“把代码翻译成另一门语言”的工具。更准确地说，它把静态命中、目录上下文和配置的 LLM provider 结合起来，给出可验证的代码阅读辅助；默认 provider 是本地 Ollama。

## 动机

很多老系统的问题不在语法本身，而在隐含约束：

- Fortran 的 `COMMON` 和 `EQUIVALENCE` 把数据布局变成了隐式契约。
- COBOL 的 `PERFORM THRU`、`REDEFINES` 和句点作用域会让控制流、数据布局依赖源码顺序和固定宽度记录。
- C/C++ 和汇编中的 bit mask、union、宏和跳转往往是性能、内存或设备协议约束下的产物。
- 现代项目中，单行 `open()`、`fetch()`、`defer`、`?`、`await`、LINQ 或管道操作，也需要结合当前目录和引用关系判断影响面。

Legacy Lens 的目标是让悬停解释尽量集中在具体代码上：它做什么、它在本目录或项目里可能承担什么职责、哪些行号和引用能支撑这个判断、下一步该看哪里。

## 架构

运行路径默认全部在本机：

```text
VS Code extension or CLI
  -> local Python backend, default http://127.0.0.1:8765
  -> static analyzers and local context scanner
  -> configured LLM provider
     -> default local Ollama
     -> optional OpenAI-compatible API
```

主要模块：

- `src/legacylens/server.py`: 本地 HTTP 服务，提供 `/health`、`/models`、`/analyze`、`/analyze/stream`。
- `src/legacylens/engine.py`: 语言识别、analyzer 调度、上下文扫描、解释生成入口。
- `src/legacylens/analyzers/`: 静态规则。遗留语言使用专用 analyzer，现代语言使用 profile-based mainstream analyzer。
- `src/legacylens/context.py`: 当前目录或项目范围内的文件和符号引用扫描。
- `src/legacylens/llm.py`: LLM provider 选择、本地 Ollama/API 调用、流式输出、行号校验。
- `vscode-extension/`: VS Code 前端，支持 hover 和聊天式流式分析面板。

Analyzer 分工：

- `CLikeAnalyzer`: C/C++ 的 goto、union、bit packing、宏、八进制字面量。
- `FortranAnalyzer`: COMMON、EQUIVALENCE、computed GOTO、arithmetic IF、label DO。
- `CobolAnalyzer`: PERFORM THRU、ALTER、GO TO、REDEFINES、OCCURS DEPENDING ON、NEXT SENTENCE。
- `AssemblyAnalyzer`: 跳转、位移/旋转、原始字节。
- `MainstreamAnalyzer`: Python、Java、Go、C#、Rust、R、JavaScript/TypeScript、SQL、shell、配置文件等现代语言 profile。规则 ID 使用语言前缀，例如 `python.file-io`、`go.goroutine`、`rust.result-propagation`。

## 使用方法

### 后端

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

指定分析输出语言：

```powershell
$env:PYTHONPATH = "src"
Get-Content sample.c | python -m legacylens analyze - --language c --output-language ja-JP --use-llm
```

## 配置

如果不提供配置文件，Legacy Lens 维持默认本地 Ollama 用法，并尝试自动发现本地模型。

配置文件查找顺序：

1. `LEGACYLENS_CONFIG` 指定的路径；只在需要临时指定配置路径时使用。
2. 从当前工作目录向上查找 `.legacylens.local.json`。
3. 从当前工作目录向上查找 `.legacylens.json`。

这两个本地配置文件默认被 `.gitignore` 排除，避免误提交 API key。

统一配置模板：

```json
{
  "outputLanguage": "auto",
  "logging": {
    "level": "INFO"
  },
  "llm": {
    "mode": "local",
    "timeoutSeconds": 60,
    "local": {
      "host": "",
      "model": "",
      "prefer": [],
      "disableAutodiscovery": false
    },
    "api": {
      "baseUrl": "",
      "path": "/chat/completions",
      "url": "",
      "apiKey": "",
      "apiKeyHeader": "Authorization",
      "apiKeyPrefix": "Bearer ",
      "model": "",
      "headers": {}
    }
  }
}
```

说明：

- `outputLanguage`: 解释输出语言。解析优先级是配置文件、VS Code locale、系统语言。
- `logging.level`: `DEBUG`、`INFO`、`WARNING`、`ERROR` 等 Python 日志级别。
- `llm.mode`: `local` 或 `api`。
- `llm.timeoutSeconds`: LLM 生成超时。
- `llm.model`: 通用模型名；`local.model` 或 `api.model` 会优先于它。
- `llm.local.host`: 本地 Ollama 地址；留空时使用默认值。
- `llm.local.model`: 本地模型名；留空时允许自动发现。
- `llm.local.prefer`: 自动发现本地模型时的偏好关键词。
- `llm.local.disableAutodiscovery`: 是否禁用本地模型自动发现。
- `llm.api.baseUrl` + `llm.api.path`: OpenAI-compatible Chat Completions API 地址。
- `llm.api.url`: 完整 API 地址；填写后优先于 `baseUrl` + `path`。
- `llm.api.apiKey`: API key。建议只放在被 `.gitignore` 排除的本地配置文件中。
- `llm.api.headers`: 额外请求头。

## 多语言输出

Legacy Lens 可以要求 LLM 使用多种语言回答。如果模型不能可靠地使用目标语言生成准确的技术分析，prompt 会要求它整段回退到英语，而不是混合多种语言。

可选输出语言：

- `auto`: 使用配置、VS Code locale 或系统语言。
- `en`: 英语。
- `zh-CN` 或 `zh-Hans`: 简体中文。
- `zh-TW`、`zh-HK` 或 `zh-Hant`: 繁体中文。
- `ja` 或 `ja-JP`: 日语。
- `ko` 或 `ko-KR`: 韩语。
- `fr` 或 `fr-FR`: 法语。
- `de` 或 `de-DE`: 德语。
- `es` 或 `es-ES`: 西班牙语。
- `pt`、`pt-BR` 或 `pt-PT`: 葡萄牙语。
- `ru` 或 `ru-RU`: 俄语。
- `it` 或 `it-IT`: 意大利语。

未知 locale 会回退到英语。确定性的本地 fallback 目前支持英语和简体中文；其他 locale 的确定性 fallback 使用英语。

## 日志

后端默认输出 INFO 级别日志。日志包含：

- LLM provider、model、host。
- 调用开始、成功、失败。
- stream/non-stream 标记。
- prompt 字符数、输出字符数、耗时。
- fallback 原因。
- 行号校验警告。

日志不会记录代码正文、prompt 正文或 API key。若 URL 查询参数中包含 `api_key`、`key` 或 `token`，日志会做脱敏。

## VS Code 扩展

安装前端依赖并编译：

```powershell
cd vscode-extension
npm install
npm run compile
```

开发调试时，在 VS Code 中打开仓库或 `vscode-extension`，按 `F5` 启动 Extension Host。

常用命令：

- `Legacy Lens: Analyze Selection`: 打开聊天式 Webview，并通过 `/analyze/stream` 一边生成一边显示。
- `Legacy Lens: Start Backend`: 启动本地 Python 后端。
- `Legacy Lens: Stop Backend`: 停止由扩展启动的后端进程。
- `Legacy Lens: Show Ollama Models`: 查看后端当前配置的 provider 和模型状态。

Hover 行为：

- hover 默认直接请求 LLM，`legacyLens.hoverUseLlm` 默认是 `true`。
- VS Code hover 无法边生成边刷新，因此 hover 会等待完整 LLM 结果后一次性显示。
- `Legacy Lens: Analyze Selection` 用流式 Webview 展示 LLM 输出，收到字符就追加显示。
- 如果希望 hover 更快返回本地静态解释，可以把 `legacyLens.hoverUseLlm` 设为 `false`。
- 扩展会把 VS Code locale 传给后端；如果配置文件里的 `outputLanguage` 不是 `auto`，配置文件优先。

主要设置：

- `legacyLens.backendUrl`: 本地后端地址，默认 `http://127.0.0.1:8765`。
- `legacyLens.maxContextLines`: hover 附近发送给后端的最大行数。
- `legacyLens.useLlm`: 是否请求配置的 LLM provider，默认 `true`。
- `legacyLens.hoverUseLlm`: hover 是否直接用 LLM，默认 `true`。
- `legacyLens.outputLanguage`: VS Code 侧输出语言设置。只有项目配置文件里的 `outputLanguage` 为 `auto` 或缺失时才会参与解析。
- `legacyLens.contextScope`: `none`、`directory` 或 `project`。
- `legacyLens.autoStartBackend`: 首次使用时是否自动启动后端。
- `legacyLens.backendCommand`、`legacyLens.backendArgs`、`legacyLens.backendCwd`: 后端启动命令、参数和工作目录。

## 流式 API

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
  outputLanguage = "zh-CN"
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

## 支持的代码语言

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

## 隐私

Legacy Lens 的默认运行路径完全在本地：

- VS Code 扩展只请求 `legacyLens.backendUrl`，默认是 `http://127.0.0.1:8765`。
- Python 后端只在本机分析代码、扫描本地目录、读取本地事实库。
- 默认 LLM 调用只发往本地 Ollama。
- 默认情况下，项目代码、目录结构、hover 片段和模型输出不会发送到外部 API。
- 扩展没有遥测逻辑，也没有远程数据上报逻辑。
- 后端日志只记录调用元数据，不记录代码正文、prompt 正文或 API key。

边界说明：

- `npm install`、未来可能的 Python 依赖安装属于开发环境准备，可能访问包管理源；这不属于运行时代码分析路径。
- 如果你主动把 `legacyLens.backendUrl` 或 `llm.local.host` 配置成远程地址，代码片段会发送到你配置的远程服务。
- 如果你把 `llm.mode` 设为 `api`，后端会把 prompt 中的代码片段、文件名、目录上下文和静态命中发送到你配置的目标 API。

## 开发

运行测试：

```powershell
python -m unittest discover -s tests -v
```

编译 VS Code 扩展：

```powershell
cd vscode-extension
npm run compile
```

建议不要提交以下内容：

- `.venv/`
- `vscode-extension/node_modules/`
- `vscode-extension/dist/`
- IDE 本地配置，例如 `.idea/`，除非项目明确决定托管它们

## 许可证

Legacy Lens 以 Apache License 2.0 发布，完整文本见仓库根目录的 `LICENSE`。包元数据使用标准 SPDX 标识 `Apache-2.0`。

再分发或修改时请保留 `LICENSE`，保留源码中已有的版权、专利、商标和归属声明，并在修改过的文件中清楚标明变更。当前仓库没有单独的 `NOTICE` 文件；如果未来加入 `NOTICE`，分发衍生作品时也需要按 Apache-2.0 要求保留其中适用的归属说明。
