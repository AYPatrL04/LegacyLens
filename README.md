# Legacy Lens

Language: [English](README.md) | [Simplified Chinese](README.zh-CN.md)

Legacy Lens is a local code-reading assistant for legacy and modern codebases. It focuses on two common workflows:

- Explaining COBOL, Fortran, C/C++, assembly, and other legacy idioms that depend on labels, shared storage, bit packing, fixed-width records, or unstructured control flow.
- Explaining what nearby code does in a modern project, and what role it appears to play in the current directory or project context.

Legacy Lens is not a code translation tool. It combines static findings, local project context, and a configured LLM provider to produce evidence-oriented explanations. The default provider path is local Ollama.

## Motivation

The hard part of older systems is often not syntax. It is the implicit contract around the code:

- Fortran `COMMON` and `EQUIVALENCE` make data layout part of program behavior.
- COBOL `PERFORM THRU`, `REDEFINES`, and period scope can make control flow and record layout depend on source order and fixed-width records.
- C/C++ and assembly bit masks, unions, macros, and jumps often encode memory, performance, or device-protocol constraints.
- Modern code such as `open()`, `fetch()`, `defer`, `?`, `await`, LINQ, or pipeline operations still needs local context to judge callers, side effects, and impact.

Legacy Lens keeps hover explanations tied to concrete evidence: what the code does, what role is supported by nearby files and references, which line numbers support the claim, and what to inspect next.

## Architecture

The default runtime path stays local:

```text
VS Code extension or CLI
  -> local Python backend, default http://127.0.0.1:8765
  -> static analyzers and local context scanner
  -> configured LLM provider
     -> default local Ollama
     -> optional OpenAI-compatible API
```

Main modules:

- `src/legacylens/server.py`: local HTTP service for `/health`, `/models`, `/analyze`, and `/analyze/stream`.
- `src/legacylens/engine.py`: language detection, analyzer dispatch, context scan, and explanation entry point.
- `src/legacylens/analyzers/`: static rules. Legacy languages use dedicated analyzers; modern languages use a profile-based mainstream analyzer.
- `src/legacylens/context.py`: local file and symbol-reference scanning for the current directory or project.
- `src/legacylens/llm.py`: LLM provider selection, local Ollama/API calls, streaming, and line-reference validation.
- `vscode-extension/`: VS Code frontend for hover and streaming analysis.

Analyzer coverage:

- `CLikeAnalyzer`: C/C++ `goto`, `union`, bit packing, macros, and octal-looking literals.
- `FortranAnalyzer`: `COMMON`, `EQUIVALENCE`, computed `GOTO`, arithmetic `IF`, and label `DO`.
- `CobolAnalyzer`: `PERFORM THRU`, `ALTER`, `GO TO`, `REDEFINES`, `OCCURS DEPENDING ON`, and `NEXT SENTENCE`.
- `AssemblyAnalyzer`: jumps, shifts/rotates, and raw bytes.
- `MainstreamAnalyzer`: Python, Java, Go, C#, Rust, R, JavaScript/TypeScript, SQL, shell, config files, and other mainstream profiles. Rule IDs use language prefixes such as `python.file-io`, `go.goroutine`, and `rust.result-propagation`.

## Usage

### Backend

Start the local backend from the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m legacylens serve --host 127.0.0.1 --port 8765
```

Check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Show the configured provider and model status:

```powershell
$env:PYTHONPATH = "src"
python -m legacylens models
```

Analyze code without an LLM:

```powershell
$env:PYTHONPATH = "src"
@'
      COMMON /A/ X, Y
      GO TO 100
'@ | python -m legacylens analyze - --language fortran --cursor-line 1
```

Analyze code with the configured LLM provider:

```powershell
$env:PYTHONPATH = "src"
@'
int flags = 0;
flags = flags | 001;
'@ | python -m legacylens analyze - --language c --cursor-line 2 --context-scope none --use-llm
```

Choose an output language for a request:

```powershell
$env:PYTHONPATH = "src"
Get-Content sample.c | python -m legacylens analyze - --language c --output-language ja-JP --use-llm
```

## Configuration

If no config file is present, Legacy Lens keeps the default local Ollama behavior and attempts local model autodiscovery.

Config discovery order:

1. `LEGACYLENS_CONFIG`, only when you need to point at a specific config path.
2. `.legacylens.local.json`, searched from the current working directory upward.
3. `.legacylens.json`, searched from the current working directory upward.

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
    "parallelSections": false,
    "parallelSectionLimit": 4,
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

Config fields:

- `outputLanguage`: explanation language. Resolution priority is config file, then VS Code locale, then system language.
- `logging.level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, and similar Python logging levels.
- `llm.mode`: `local` or `api`.
- `llm.timeoutSeconds`: generation timeout.
- `llm.parallelSections`: when true, split the final explanation into multiple section prompts and generate them concurrently.
- `llm.parallelSectionLimit`: maximum number of concurrent section requests when `parallelSections` is enabled.
- `llm.model`: general model name. `local.model` or `api.model` takes precedence.
- `llm.local.host`: local Ollama host. Leave empty for the default.
- `llm.local.model`: local model name. Leave empty to allow local autodiscovery.
- `llm.local.prefer`: model-name preferences for autodiscovery.
- `llm.local.disableAutodiscovery`: disables local model autodiscovery when true.
- `llm.api.baseUrl` plus `llm.api.path`: OpenAI-compatible Chat Completions endpoint.
- `llm.api.url`: full API endpoint. If present, it takes precedence over `baseUrl` plus `path`.
- `llm.api.apiKey`: API key. Keep it only in ignored local config files.
- `llm.api.headers`: extra request headers.

## Multilingual Output

Legacy Lens can ask the LLM to answer in multiple languages. If the selected model cannot reliably produce accurate technical analysis in the target language, the prompt instructs it to use English for the whole answer instead of mixing languages.

Supported output language values:

- `auto`: use config, VS Code locale, or system language.
- `en`: English.
- `zh-CN` or `zh-Hans`: Simplified Chinese.
- `zh-TW`, `zh-HK`, or `zh-Hant`: Traditional Chinese.
- `ja` or `ja-JP`: Japanese.
- `ko` or `ko-KR`: Korean.
- `fr` or `fr-FR`: French.
- `de` or `de-DE`: German.
- `es` or `es-ES`: Spanish.
- `pt`, `pt-BR`, or `pt-PT`: Portuguese.
- `ru` or `ru-RU`: Russian.
- `it` or `it-IT`: Italian.

Unknown locales fall back to English. The deterministic local fallback is localized for English and Simplified Chinese; other deterministic fallback output uses English.

## Logging

The backend logs LLM call metadata at `INFO` level by default:

- provider, model, and host.
- call start, success, failure, and fallback.
- stream vs non-stream.
- prompt character count, output character count, and elapsed time.
- line-reference warnings.

Logs do not include source code, prompt text, or API keys. Query parameters named `api_key`, `key`, or `token` are redacted.

## VS Code Extension

Install dependencies and compile:

```powershell
cd vscode-extension
npm install
npm run compile
```

For development, open the repository or `vscode-extension` in VS Code and press `F5` to start an Extension Host.

Commands:

- `Legacy Lens: Analyze Selection`: opens a streaming analysis webview through `/analyze/stream`.
- `Legacy Lens: Start Backend`: starts the local Python backend.
- `Legacy Lens: Stop Backend`: stops the backend process started by the extension.
- `Legacy Lens: Show Ollama Models`: shows provider and model status.

Hover behavior:

- Hover uses the configured LLM provider by default when `legacyLens.hoverUseLlm` is true.
- VS Code hover cannot stream partial output, so it waits for the full response.
- `Legacy Lens: Analyze Selection` uses a streaming webview and appends tokens as they arrive.
- Set `legacyLens.hoverUseLlm` to false for faster deterministic hover output.
- The extension sends the VS Code locale to the backend. If config `outputLanguage` is not `auto`, the config file wins.

Important settings:

- `legacyLens.backendUrl`: local backend URL, default `http://127.0.0.1:8765`.
- `legacyLens.maxContextLines`: maximum context lines around the hover position.
- `legacyLens.useLlm`: whether analysis asks the configured LLM provider.
- `legacyLens.hoverUseLlm`: whether hover waits for LLM output.
- `legacyLens.outputLanguage`: VS Code-side output-language setting. It participates only when project config `outputLanguage` is `auto` or absent.
- `legacyLens.contextScope`: `none`, `directory`, or `project`.
- `legacyLens.autoStartBackend`: automatically start the backend when needed.
- `legacyLens.backendCommand`, `legacyLens.backendArgs`, `legacyLens.backendCwd`: backend startup command, arguments, and working directory.

## Streaming API

`POST /analyze/stream` returns newline-delimited JSON events:

- `metadata`: static findings, context, and model status.
- `delta`: generated text chunk.
- `fallback`: local fallback reason.
- `done`: final event.

Request example:

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

## Supported Code Languages

Legacy and systems languages:

- C/C++/Objective-C: `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`, `.m`, `.mm`
- Fortran: `.f`, `.f77`, `.for`, `.ftn`
- COBOL: `.cob`, `.cbl`, `.cpy`
- Assembly: `.asm`, `.s`

Modern mainstream languages:

- Python, Java, Go, C#, Rust, R
- JavaScript, TypeScript, PHP, Ruby, Kotlin, Swift, Scala, SQL
- shell, PowerShell, Batch, Lua, Perl, Haskell, Dart, Elixir, Erlang, Clojure, F#, Julia, Visual Basic, Groovy
- HTML, CSS, SCSS, Sass, Less, Vue, Svelte
- JSON, YAML, TOML, XML, Markdown, Dockerfile

## Privacy

The default runtime path is local:

- The VS Code extension only calls `legacyLens.backendUrl`, default `http://127.0.0.1:8765`.
- The Python backend analyzes code and scans local context on the same machine.
- The default LLM path calls local Ollama.
- By default, project code, directory structure, hover snippets, and model output are not sent to an external API.
- The extension has no telemetry or remote reporting logic.
- Backend logs record metadata only, not source code, prompt text, or API keys.

Boundaries:

- `npm install` and future dependency installation may contact package registries. That is development setup, not the runtime code-analysis path.
- If `legacyLens.backendUrl` or `llm.local.host` is configured to a remote address, snippets are sent to that configured service.
- If `llm.mode` is set to `api`, the backend sends prompt content, file names, directory context, and static findings to the configured API.

## Development

Run tests:

```powershell
python -m unittest discover -s tests -v
```

Compile the VS Code extension:

```powershell
cd vscode-extension
npm run compile
```

Do not commit:

- `.venv/`
- `vscode-extension/node_modules/`
- `vscode-extension/dist/`
- local IDE files such as `.idea/`, unless the project explicitly decides to track them

## License

Legacy Lens is released under the Apache License 2.0. The full license text is in `LICENSE`, and package metadata uses the SPDX identifier `Apache-2.0`.

When redistributing or modifying the project, keep `LICENSE`, retain applicable copyright, patent, trademark, and attribution notices, and clearly mark modified files. The repository currently has no separate `NOTICE` file; if one is added later, redistributed derivative works should keep applicable notices as required by Apache-2.0.
