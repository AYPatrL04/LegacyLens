from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import logging_level
from .engine import LegacyLensEngine
from .llm import DEFAULT_OLLAMA_HOST, list_ollama_models
from .models import AnalysisRequest


LOGGER = logging.getLogger("legacylens.server")


class LegacyLensRequestHandler(BaseHTTPRequestHandler):
    engine = LegacyLensEngine()
    server_version = "LegacyLens/0.1"

    def do_OPTIONS(self) -> None:
        self._send_json(HTTPStatus.NO_CONTENT, None)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "legacy-lens",
                    "llm": self.engine.explainer.model_status(),
                },
            )
            return
        if self.path == "/models":
            self._handle_models()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/analyze":
                self._handle_analyze(payload)
                return
            if self.path == "/analyze/stream":
                self._handle_analyze_stream(payload)
                return
            if self.path == "/rpc":
                self._handle_rpc(payload)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_analyze(self, payload: dict[str, Any]) -> None:
        request = AnalysisRequest.from_mapping(payload)
        if not request.code.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "code is required"})
            return
        response = self.engine.analyze(request)
        self._send_json(HTTPStatus.OK, response.to_dict())

    def _handle_analyze_stream(self, payload: dict[str, Any]) -> None:
        request = AnalysisRequest.from_mapping(payload)
        if not request.code.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "code is required"})
            return

        inspected = self.engine.inspect(request)
        self._start_ndjson_stream()
        try:
            self._write_ndjson(
                {
                    "type": "metadata",
                    "language": inspected.language,
                    "output_language": inspected.output_language,
                    "findings": [finding.to_dict() for finding in inspected.findings],
                    "context": inspected.context.to_dict() if inspected.context else None,
                    "excerpt_start_line": request.excerpt_start_line,
                    "cursor_line": request.cursor_line,
                    "llm": self.engine.explainer.model_status(),
                }
            )
            for event in self.engine.explainer.explain_stream(
                request,
                language=inspected.language,
                findings=inspected.findings,
                context=inspected.context,
            ):
                self._write_ndjson(event)
        except BrokenPipeError:
            return
        except OSError:
            return

    def _handle_rpc(self, payload: dict[str, Any]) -> None:
        rpc_id = payload.get("id")
        method = payload.get("method")
        if method != "legacyLens.analyze":
            self._send_json(
                HTTPStatus.OK,
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}},
            )
            return
        params = payload.get("params") or {}
        request = AnalysisRequest.from_mapping(params)
        response = self.engine.analyze(request)
        self._send_json(HTTPStatus.OK, {"jsonrpc": "2.0", "id": rpc_id, "result": response.to_dict()})

    def _handle_models(self) -> None:
        status = self.engine.explainer.model_status()
        if status.get("provider") == "api":
            selected = status.get("model")
            self._send_json(
                HTTPStatus.OK,
                {
                    "provider": "api",
                    "models": [selected] if selected else [],
                    "selected": selected,
                    "llm": status,
                },
            )
            return

        host = status.get("host") or DEFAULT_OLLAMA_HOST
        try:
            models = list_ollama_models(str(host))
            self._send_json(HTTPStatus.OK, {"provider": "ollama", "models": models, "selected": status.get("model"), "llm": status})
        except OSError as exc:
            self._send_json(HTTPStatus.OK, {"provider": "ollama", "models": [], "selected": status.get("model"), "llm": status, "error": str(exc)})

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("JSON object expected")
        return decoded

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _start_ndjson_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()

    def _write_ndjson(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    _configure_logging()
    httpd = ThreadingHTTPServer((host, port), LegacyLensRequestHandler)
    LOGGER.info("backend listening url=http://%s:%s", host, port)
    print(f"Legacy Lens backend listening on http://{host}:{port}")
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Legacy Lens HTTP backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port)
    return 0


def _configure_logging() -> None:
    level_name = logging_level()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


if __name__ == "__main__":
    raise SystemExit(main())
