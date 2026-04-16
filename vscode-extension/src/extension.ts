import * as childProcess from "child_process";
import * as path from "path";
import * as vscode from "vscode";

type AnalyzeResponse = {
  language: string;
  markdown: string;
  findings: Array<{
    title: string;
    severity: string;
    span: { start_line: number; end_line: number; text: string };
  }>;
};

type StreamEvent = {
  type: "metadata" | "delta" | "fallback" | "done" | "error";
  text?: string;
  reason?: string;
  model_used?: string | null;
  fallback_reason?: string | null;
  language?: string;
  excerpt_start_line?: number;
  cursor_line?: number;
  findings?: Array<{ title: string; severity: string; span: { start_line: number; text: string } }>;
  llm?: { available: boolean; model?: string | null; host?: string | null };
};

let backendProcess: childProcess.ChildProcess | undefined;
let outputChannel: vscode.OutputChannel;

const SUPPORTED_LANGUAGES = [
  "asm",
  "batch",
  "c",
  "clojure",
  "cobol",
  "cpp",
  "csharp",
  "css",
  "dart",
  "dockerfile",
  "elixir",
  "erlang",
  "fortran",
  "fsharp",
  "go",
  "groovy",
  "haskell",
  "html",
  "java",
  "javascript",
  "javascriptreact",
  "json",
  "jsonc",
  "julia",
  "kotlin",
  "less",
  "lua",
  "markdown",
  "objective-c",
  "objective-cpp",
  "perl",
  "php",
  "powershell",
  "python",
  "r",
  "ruby",
  "rust",
  "sass",
  "scala",
  "scss",
  "shellscript",
  "sql",
  "svelte",
  "swift",
  "toml",
  "typescript",
  "typescriptreact",
  "vb",
  "vue",
  "xml",
  "yaml",
];

export function activate(context: vscode.ExtensionContext) {
  outputChannel = vscode.window.createOutputChannel("Legacy Lens");
  context.subscriptions.push(outputChannel);

  const selector: vscode.DocumentSelector = SUPPORTED_LANGUAGES.map((language) => ({ language }));

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(selector, {
      async provideHover(document, position, token) {
        const config = vscode.workspace.getConfiguration("legacyLens");
        const response = await analyzeDocumentContext(
          document,
          position,
          token,
          config.get<boolean>("hoverUseLlm", false),
        );
        if (!response || !response.markdown) {
          return undefined;
        }
        const markdown = new vscode.MarkdownString(response.markdown);
        markdown.supportHtml = false;
        markdown.isTrusted = false;
        return new vscode.Hover(markdown);
      },
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("legacyLens.analyzeSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        return;
      }
      await analyzeDocumentContextStream(editor);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("legacyLens.startBackend", async () => {
      const config = vscode.workspace.getConfiguration("legacyLens");
      const backendUrl = getBackendUrl(config);
      if (await ensureBackend(backendUrl, config)) {
        vscode.window.showInformationMessage("Legacy Lens backend is running.");
      } else {
        vscode.window.showWarningMessage("Legacy Lens backend did not become ready.");
      }
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("legacyLens.stopBackend", () => {
      stopBackend();
      vscode.window.showInformationMessage("Legacy Lens backend process stopped.");
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("legacyLens.showModels", async () => {
      const config = vscode.workspace.getConfiguration("legacyLens");
      const backendUrl = getBackendUrl(config);
      if (!(await ensureBackend(backendUrl, config))) {
        vscode.window.showWarningMessage("Legacy Lens backend is not available.");
        return;
      }
      const response = await fetchJson<{ models: string[]; error?: string }>(`${backendUrl}/models`);
      if (!response || response.error) {
        vscode.window.showWarningMessage(`Legacy Lens could not list Ollama models: ${response?.error ?? "unknown error"}`);
        return;
      }
      vscode.window.showInformationMessage(`Legacy Lens Ollama models: ${response.models.join(", ") || "none"}`);
    }),
  );
}

export function deactivate() {
  stopBackend();
}

async function analyzeDocumentContext(
  document: vscode.TextDocument,
  position: vscode.Position,
  token?: vscode.CancellationToken,
  useLlmOverride?: boolean,
): Promise<AnalyzeResponse | undefined> {
  const config = vscode.workspace.getConfiguration("legacyLens");
  const backendUrl = getBackendUrl(config);
  if (!(await ensureBackend(backendUrl, config))) {
    return undefined;
  }

  const body = buildAnalyzeBody(document, position, config, useLlmOverride);
  try {
    const response = await fetch(`${backendUrl}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: token ? abortSignalFromCancellation(token) : undefined,
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as AnalyzeResponse;
  } catch (error) {
    outputChannel.appendLine(`Analyze failed: ${String(error)}`);
    return undefined;
  }
}

async function analyzeDocumentContextStream(editor: vscode.TextEditor): Promise<void> {
  const config = vscode.workspace.getConfiguration("legacyLens");
  const backendUrl = getBackendUrl(config);
  if (!(await ensureBackend(backendUrl, config))) {
    vscode.window.showWarningMessage("Legacy Lens backend is not available.");
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    "legacyLensStreamingAnalysis",
    "Legacy Lens",
    vscode.ViewColumn.Beside,
    { enableScripts: true },
  );
  panel.webview.html = streamingWebviewHtml();

  const body = buildAnalyzeBody(editor.document, editor.selection.active, config, true, editor.selection);
  panel.webview.postMessage({ type: "reset", fileName: editor.document.fileName });

  try {
    const response = await fetch(`${backendUrl}/analyze/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok || !response.body) {
      panel.webview.postMessage({ type: "error", text: `Backend returned HTTP ${response.status}` });
      return;
    }
    await consumeNdjsonStream(response, (event) => {
      panel.webview.postMessage(event);
    });
  } catch (error) {
    panel.webview.postMessage({ type: "error", text: String(error) });
    outputChannel.appendLine(`Streaming analyze failed: ${String(error)}`);
  }
}

function buildAnalyzeBody(
  document: vscode.TextDocument,
  position: vscode.Position,
  config: vscode.WorkspaceConfiguration,
  useLlmOverride?: boolean,
  selection?: vscode.Selection,
) {
  const maxContextLines = config.get<number>("maxContextLines", 80);
  const useLlm = useLlmOverride ?? config.get<boolean>("useLlm", true);
  const contextScope = config.get<string>("contextScope", "directory");
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);

  const hasSelection = selection && !selection.isEmpty;
  const lineWindow = Math.max(4, Math.floor(maxContextLines / 2));
  const start = hasSelection ? selection.start.line : Math.max(0, position.line - lineWindow);
  const end = hasSelection ? selection.end.line : Math.min(document.lineCount - 1, position.line + lineWindow);
  const range = hasSelection
    ? selection
    : new vscode.Range(start, 0, end, document.lineAt(end).text.length);

  return {
    code: document.getText(range),
    language: document.languageId,
    fileName: document.fileName,
    projectRoot: workspaceFolder?.uri.fsPath,
    excerptStartLine: start + 1,
    cursorLine: position.line + 1,
    maxFindings: 6,
    useLlm,
    contextScope,
  };
}

async function ensureBackend(backendUrl: string, config: vscode.WorkspaceConfiguration): Promise<boolean> {
  if (await isBackendHealthy(backendUrl)) {
    return true;
  }

  if (!config.get<boolean>("autoStartBackend", true)) {
    return false;
  }

  if (!backendProcess || backendProcess.killed || backendProcess.exitCode !== null) {
    startBackend(config);
  }

  return waitForBackend(backendUrl, 15000);
}

function startBackend(config: vscode.WorkspaceConfiguration) {
  const command = config.get<string>("backendCommand", "python");
  const args = config.get<string[]>("backendArgs", [
    "-m",
    "legacylens",
    "serve",
    "--host",
    "127.0.0.1",
    "--port",
    "8765",
  ]);
  const workspaceFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const configuredCwd = config.get<string>("backendCwd", "");
  const cwd = configuredCwd || workspaceFolder || undefined;
  const env = { ...process.env };
  if (workspaceFolder) {
    const srcPath = path.join(workspaceFolder, "src");
    env.PYTHONPATH = env.PYTHONPATH ? `${srcPath}${path.delimiter}${env.PYTHONPATH}` : srcPath;
  }

  outputChannel.appendLine(`Starting backend: ${command} ${args.join(" ")}`);
  backendProcess = childProcess.spawn(command, args, { cwd, env });
  backendProcess.stdout?.on("data", (chunk) => outputChannel.append(chunk.toString()));
  backendProcess.stderr?.on("data", (chunk) => outputChannel.append(chunk.toString()));
  backendProcess.on("exit", (code, signal) => {
    outputChannel.appendLine(`Legacy Lens backend exited with code=${code} signal=${signal}`);
  });
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
  backendProcess = undefined;
}

async function waitForBackend(backendUrl: string, timeoutMs: number): Promise<boolean> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await isBackendHealthy(backendUrl)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

async function isBackendHealthy(backendUrl: string): Promise<boolean> {
  const response = await fetchJson<{ ok: boolean }>(`${backendUrl}/health`, 1500);
  return response?.ok === true;
}

async function fetchJson<T>(url: string, timeoutMs = 5000): Promise<T | undefined> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as T;
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

async function consumeNdjsonStream(response: Response, onEvent: (event: StreamEvent) => void): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) {
    onEvent({ type: "error", text: "Streaming response body is not readable." });
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        try {
          onEvent(JSON.parse(line) as StreamEvent);
        } catch (error) {
          outputChannel.appendLine(`Could not parse stream event: ${String(error)}; line=${line}`);
        }
      }
      newlineIndex = buffer.indexOf("\n");
    }
  }
  const tail = buffer.trim();
  if (tail) {
    try {
      onEvent(JSON.parse(tail) as StreamEvent);
    } catch (error) {
      outputChannel.appendLine(`Could not parse final stream event: ${String(error)}; line=${tail}`);
    }
  }
}

function streamingWebviewHtml(): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      color-scheme: light dark;
      --bg: var(--vscode-editor-background);
      --fg: var(--vscode-editor-foreground);
      --muted: var(--vscode-descriptionForeground);
      --border: var(--vscode-panel-border);
      --bubble: var(--vscode-editorWidget-background);
      --accent: var(--vscode-textLink-foreground);
    }
    body {
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--vscode-font-family);
    }
    .shell {
      max-width: 920px;
      margin: 0 auto;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 14px;
      word-break: break-all;
    }
    .bubble {
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--bubble);
      padding: 14px 16px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
    }
    .role {
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 10px;
    }
    #answer {
      white-space: pre-wrap;
      line-height: 1.55;
      font-size: 14px;
    }
    .cursor {
      display: inline-block;
      width: 7px;
      height: 1em;
      margin-left: 2px;
      transform: translateY(2px);
      background: var(--accent);
      animation: blink 1s steps(2, start) infinite;
    }
    .status {
      color: var(--muted);
      margin-top: 12px;
      font-size: 12px;
    }
    .error {
      color: var(--vscode-errorForeground);
    }
    @keyframes blink {
      to { visibility: hidden; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div id="meta" class="meta">Legacy Lens is starting...</div>
    <section class="bubble">
      <div class="role">Legacy Lens</div>
      <div id="answer"></div><span id="cursor" class="cursor"></span>
      <div id="status" class="status">等待模型输出...</div>
    </section>
  </main>
  <script>
    const meta = document.getElementById('meta');
    const answer = document.getElementById('answer');
    const status = document.getElementById('status');
    const cursor = document.getElementById('cursor');
    window.addEventListener('message', (event) => {
      const message = event.data;
      if (message.type === 'reset') {
        answer.textContent = '';
        meta.textContent = message.fileName || '';
        status.textContent = '正在分析上下文...';
        cursor.style.display = 'inline-block';
      } else if (message.type === 'metadata') {
        const model = message.llm && message.llm.model ? message.llm.model : 'deterministic fallback';
        const count = Array.isArray(message.findings) ? message.findings.length : 0;
        const line = message.cursor_line ? ' | 行: ' + message.cursor_line : '';
        status.textContent = '语言: ' + (message.language || 'unknown') + line + ' | 命中: ' + count + ' | 模型: ' + model;
      } else if (message.type === 'delta') {
        answer.textContent += message.text || '';
        window.scrollTo(0, document.body.scrollHeight);
      } else if (message.type === 'fallback') {
        status.textContent = '回退到本地解释: ' + (message.reason || '');
      } else if (message.type === 'done') {
        cursor.style.display = 'none';
        const fallback = message.fallback_reason ? ' | fallback: ' + message.fallback_reason : '';
        status.textContent = '完成' + fallback;
      } else if (message.type === 'error') {
        cursor.style.display = 'none';
        status.textContent = message.text || 'Unknown error';
        status.className = 'status error';
      }
    });
  </script>
</body>
</html>`;
}

function getBackendUrl(config: vscode.WorkspaceConfiguration): string {
  return config.get<string>("backendUrl", "http://127.0.0.1:8765").replace(/\/$/, "");
}

function abortSignalFromCancellation(token: vscode.CancellationToken): AbortSignal {
  const controller = new AbortController();
  if (token.isCancellationRequested) {
    controller.abort();
  }
  token.onCancellationRequested(() => controller.abort());
  return controller.signal;
}
