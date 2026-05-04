# Legacy Lens Architecture

This repository now contains a runnable MVP that follows the README's layered design while keeping hard dependencies optional.

## Runtime Path

1. VSCode hover or CLI sends a source snippet to the local Python backend.
2. The backend detects the language from an explicit hint, file extension, or content.
3. A language analyzer emits structured findings with rule IDs, line spans, tags, and code-behavior rationale.
4. When requested, the context scanner adds current-directory or project file context plus same-language symbol references.
5. The explainer returns deterministic Markdown when LLM use is disabled, or calls the configured LLM provider when the request enables LLM use. The default provider is local Ollama; config can switch it to an OpenAI-compatible API.
6. When `llm.parallelSections` is enabled, the explainer splits the final answer into section-specific prompts and runs them concurrently before merging them back into one Markdown response.

## Current MVP

- Python package: `src/legacylens`
- HTTP API: `POST /analyze`, `POST /rpc`, `GET /health`
- Streaming API: `POST /analyze/stream` returns NDJSON events (`metadata`, `delta`, `fallback`, `done`) for chatbot-like clients.
- LLM integration: defaults to local Ollama and auto-discovers a model from `/api/tags`; optional config can switch to an OpenAI-compatible Chat Completions API with a user-provided URL and API key.
- LLM logging: records provider/model/host, call start/success/failure, fallback reason, stream mode, output size, and elapsed time without logging source text or API keys.
- CLI: `legacylens analyze` and `legacylens serve`
- Context modes: `none`, `directory`, and `project`, with conservative role inference.
- VSCode extension: hover provider, streaming analyze-selection command, backend auto-start, context-scope setting, hover LLM setting, and model listing command.

## Extension Points

- Replace heuristic analyzers with Tree-sitter or ANTLR adapters behind the existing `Analyzer.analyze()` contract.
- Add richer LLM providers beside local Ollama and the OpenAI-compatible API client.
- Persist user feedback as training or retrieval data for later fine-tuning.
