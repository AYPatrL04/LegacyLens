# Legacy Lens Architecture

This repository now contains a runnable MVP that follows the README's layered design while keeping hard dependencies optional.

## Runtime Path

1. VSCode hover or CLI sends a source snippet to the local Python backend.
2. The backend detects the language from an explicit hint, file extension, or content.
3. A language analyzer emits structured findings with rule IDs, line spans, tags, and code-behavior rationale.
4. When requested, the context scanner adds current-directory or project file context plus same-language symbol references.
5. The fact store retrieves matching idiom notes from `legacy_facts.jsonl`.
6. The explainer returns deterministic Markdown when LLM use is disabled, or calls local Ollama when the request enables LLM use and a local model is configured or auto-discovered.

## Current MVP

- Python package: `src/legacylens`
- HTTP API: `POST /analyze`, `POST /rpc`, `GET /health`
- Streaming API: `POST /analyze/stream` returns NDJSON events (`metadata`, `delta`, `fallback`, `done`) for chatbot-like clients.
- Ollama integration: auto-discovers a model from `/api/tags`, with `LEGACYLENS_OLLAMA_MODEL` as an explicit override.
- CLI: `legacylens analyze` and `legacylens serve`
- Context modes: `none`, `directory`, and `project`, with conservative role inference.
- VSCode extension: hover provider, streaming analyze-selection command, backend auto-start, context-scope setting, hover LLM setting, and model listing command.
- Seed facts: `src/legacylens/data/legacy_facts.jsonl`

## Extension Points

- Replace heuristic analyzers with Tree-sitter or ANTLR adapters behind the existing `Analyzer.analyze()` contract.
- Replace `FactStore` with ChromaDB while keeping `retrieve(findings, query, limit)`.
- Add richer LLM providers beside `OllamaClient`.
- Persist user feedback as training or retrieval data for later fine-tuning.
