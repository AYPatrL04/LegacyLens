# Running Legacy Lens Locally

## Backend

From the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m legacylens serve --host 127.0.0.1 --port 8765
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Show the configured LLM provider and visible models:

```powershell
$env:PYTHONPATH = "src"
python -m legacylens models
```

Analyze inline code without LLM:

```powershell
$env:PYTHONPATH = "src"
@'
      COMMON /A/ X, Y
      GO TO 100
'@ | python -m legacylens analyze - --language fortran --cursor-line 1
```

Analyze inline code with the configured LLM provider:

```powershell
$env:PYTHONPATH = "src"
@'
int flags = 0;
flags = flags | 001;
'@ | python -m legacylens analyze - --language c --cursor-line 2 --context-scope none --use-llm
```

Choose an analysis output language. `auto` uses the config file, VS Code locale, or the backend system language; model prompts tell the model to fall back to English if it cannot reliably answer in the target language:

```powershell
$env:PYTHONPATH = "src"
Get-Content sample.c | python -m legacylens analyze - --language c --output-language ja-JP --use-llm
```

Streaming analysis endpoint:

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

`/analyze/stream` returns newline-delimited JSON events:

- `metadata`: static analysis, context, and model status. This is sent before LLM generation.
- `delta`: one generated text chunk. Clients should append it immediately.
- `fallback`: why the backend fell back to deterministic local output.
- `done`: final model and fallback status.

Line numbers:

- VSCode sends both `cursorLine` and `excerptStartLine`, so backend findings are mapped to real file line numbers.
- Prompts include a numbered excerpt with real file line numbers.
- The LLM is instructed to cite only evidence lines: the hovered line, static-analysis hit lines, or symbol-reference lines.
- If generated text mentions an unsupported line number, the backend appends a `行号校验` warning.

Output language:

- Config files may set `outputLanguage`, accepting values like `en`, `zh-CN`, `zh-TW`, `ja-JP`, `fr`, or `de`.
- Resolution order is config `outputLanguage`, then VS Code locale, then backend system language.
- Request `outputLanguage` is only used when config `outputLanguage` is `auto` or absent.
- LLM prompts ask for the target language directly and instruct the model to use English for the whole answer if it cannot write accurate technical analysis in that language. The deterministic local fallback is localized for English and Simplified Chinese; other locales use English.

Context modes:

- `--context-scope none`: explain only the supplied snippet.
- `--context-scope directory`: include the current file's directory tree and same-language symbol references.
- `--context-scope project`: include the detected project root, with more files and references.

The model is instructed not to infer a project role from co-location alone. If no related files or symbol references are found, the hover should say that the snippet appears standalone in the supplied context.

Supported language suffixes include legacy languages plus mainstream project files:

- C/C++/Objective-C: `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`, `.m`, `.mm`
- Legacy: `.f`, `.f77`, `.for`, `.ftn`, `.cob`, `.cbl`, `.cpy`, `.asm`, `.s`
- Requested mainstream languages: `.py`, `.java`, `.go`, `.cs`, `.rs`, `.r`
- Web/backend: `.js`, `.jsx`, `.ts`, `.tsx`, `.php`, `.rb`, `.kt`, `.kts`, `.swift`, `.scala`, `.sql`
- Scripting/systems: `.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`, `.lua`, `.pl`, `.pm`
- Other common languages: `.dart`, `.hs`, `.ex`, `.erl`, `.clj`, `.groovy`, `.fs`, `.vb`, `.jl`
- Project/config/docs: `.html`, `.css`, `.scss`, `.sass`, `.less`, `.vue`, `.svelte`, `.json`, `.yaml`, `.toml`, `.xml`, `.md`, `Dockerfile`

Languages without a dedicated legacy analyzer use the profile-based `MainstreamAnalyzer`. It still shares common behavior rules where appropriate, but emitted rule IDs stay language-specific, such as `python.file-io`, `go.goroutine`, or `rust.result-propagation`. The LLM then explains the hovered code using those behavior signals plus directory/project context.

By default, the backend uses local Ollama and auto-discovers a model through the default local Ollama endpoint.

## Configuration File

If no config file is present, Legacy Lens keeps the current local Ollama behavior.

Config discovery order:

1. `LEGACYLENS_CONFIG`, only when you need to point at a specific config path.
2. `.legacylens.local.json` found from the current working directory upward.
3. `.legacylens.json` found from the current working directory upward.

Both local config filenames are ignored by this repository's `.gitignore` because they may contain API keys.

Unified template:

```json
{
  "outputLanguage": "auto",
  "logging": {
    "level": "INFO"
  },
  "llm": {
    "mode": "local",
    "timeoutSeconds": 60,
    "model": "",
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

Set `llm.mode` to `local` for Ollama or `api` for an OpenAI-compatible Chat Completions endpoint. `local.model` and `api.model` are optional; if omitted, local mode can auto-discover a model and API mode omits the `model` field from the request body.

## Logging

The backend logs LLM call metadata at `INFO` level by default:

- provider, model, and host.
- call start, success, failure, and fallback.
- stream vs non-stream.
- prompt character count, output character count, and elapsed milliseconds.
- line-reference warnings.

Logs do not include source code, prompt text, or API keys. Query parameters named `api_key`, `key`, or `token` are redacted.

Set the level with `logging.level` in the config file.

## VSCode Extension

Install and compile:

```powershell
cd vscode-extension
npm install
npm run compile
```

Open `vscode-extension` in VSCode and run the extension host with `F5`.

The extension can auto-start the backend with:

```text
python -m legacylens serve --host 127.0.0.1 --port 8765
```

When the repository root is open as the workspace, the extension prepends `<workspace>/src` to `PYTHONPATH`, so an editable install is not required.

Commands:

- `Legacy Lens: Analyze Selection`
- `Legacy Lens: Start Backend`
- `Legacy Lens: Stop Backend`
- `Legacy Lens: Show Ollama Models`

Important settings:

- `legacyLens.backendUrl`: backend URL, default `http://127.0.0.1:8765`.
- `legacyLens.useLlm`: request the configured LLM provider, default `true`.
- `legacyLens.hoverUseLlm`: use the configured LLM provider directly inside hover, default `true`. VSCode hover cannot stream partial output, so set this to `false` if you prefer faster deterministic hover text.
- `legacyLens.outputLanguage`: VS Code-side output language setting, default `auto`. It is only used when project config `outputLanguage` is `auto` or absent.
- `legacyLens.contextScope`: `none`, `directory`, or `project`; default `directory`.
- `legacyLens.autoStartBackend`: start backend on first hover or command, default `true`.
- `legacyLens.backendCommand`: command used to start backend, default `python`.
- `legacyLens.backendArgs`: arguments for backend startup.
- `legacyLens.backendCwd`: working directory for backend startup. Empty means first workspace folder.


