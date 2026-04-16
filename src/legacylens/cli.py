from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .engine import LegacyLensEngine
from .llm import DEFAULT_OLLAMA_HOST, Explainer, list_ollama_models, select_preferred_model
from .models import AnalysisRequest
from .server import run_server


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="legacylens", description="Explain legacy code idioms.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a file or stdin.")
    analyze_parser.add_argument("path", help="Source path, or '-' for stdin.")
    analyze_parser.add_argument("--language", help="Override language detection.")
    analyze_parser.add_argument("--cursor-line", type=int, help="Rank findings near this one-based line.")
    analyze_parser.add_argument("--max-findings", type=int, default=8)
    analyze_parser.add_argument("--use-llm", action="store_true", help="Use the configured LLM provider.")
    analyze_parser.add_argument(
        "--context-scope",
        choices=("none", "directory", "project"),
        default="directory",
        help="Add current directory or project context to the explanation.",
    )
    analyze_parser.add_argument("--project-root", help="Project root used when --context-scope=project.")
    analyze_parser.add_argument("--format", choices=("json", "markdown"), default="markdown")

    serve_parser = subparsers.add_parser("serve", help="Run the local HTTP service.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    models_parser = subparsers.add_parser("models", help="Show configured LLM model status.")
    models_parser.add_argument("--host", default=None, help="Ollama host override.")

    args = parser.parse_args(argv)
    if args.command == "serve":
        run_server(host=args.host, port=args.port)
        return 0
    if args.command == "analyze":
        return _analyze_command(args)
    if args.command == "models":
        return _models_command(args)
    parser.error("unknown command")
    return 2


def _analyze_command(args: argparse.Namespace) -> int:
    if args.path == "-":
        code = sys.stdin.read()
        file_name = None
    else:
        path = Path(args.path)
        code = path.read_text(encoding="utf-8", errors="replace")
        file_name = str(path)

    request = AnalysisRequest(
        code=code,
        language=args.language,
        file_name=file_name,
        project_root=args.project_root,
        cursor_line=args.cursor_line,
        max_findings=args.max_findings,
        use_llm=args.use_llm,
        context_scope=args.context_scope,
    )
    response = LegacyLensEngine().analyze(request)
    if args.format == "json":
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(response.markdown)
    return 0


def _configure_logging() -> None:
    level_name = os.environ.get("LEGACYLENS_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def _models_command(args: argparse.Namespace) -> int:
    status = Explainer().model_status()
    if status.get("provider") == "api" and not args.host:
        selected = status.get("model")
        print(
            json.dumps(
                {"provider": "api", "models": [selected] if selected else [], "selected": selected, "llm": status},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    host = args.host or str(status.get("host") or DEFAULT_OLLAMA_HOST)
    models = list_ollama_models(host)
    selected = status.get("model") or select_preferred_model(models)
    print(
        json.dumps(
            {"provider": "ollama", "models": models, "selected": selected, "llm": status},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
