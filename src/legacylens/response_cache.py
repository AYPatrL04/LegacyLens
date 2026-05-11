from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AnalysisRequest, AnalysisResponse, Finding, ProjectContext, Severity, SourceSpan

MEMORY_CACHE_TTL_SECONDS = 180.0
MEMORY_CACHE_MAX_ENTRIES = 256
REDIS_CACHE_TTL_SECONDS = 21_600
REDIS_CACHE_MAX_ENTRIES = 2_048
REDIS_DEFAULT_HOST = "127.0.0.1"
REDIS_DEFAULT_PORT = 6379
REDIS_KEY_PREFIX = "legacylens:hover:"
REDIS_INDEX_KEY = f"{REDIS_KEY_PREFIX}index"
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class _ResponseCacheEntry:
    built_at: float
    response: AnalysisResponse
    request_digest: str


_RESPONSE_CACHE: dict[str, _ResponseCacheEntry] = {}
_RESPONSE_CACHE_LOCK = threading.Lock()
_REDIS_CLIENT: "_RedisCacheClient | None" = None
_REDIS_CLIENT_LOCK = threading.Lock()


def load_cached_response(request: AnalysisRequest) -> AnalysisResponse | None:
    cache_key = _position_cache_key(request)
    request_digest = _request_digest(request)
    redis_client = _redis_client()
    if redis_client is not None:
        cached = redis_client.get(cache_key, request_digest)
        if cached is not None:
            return cached

    now = time.monotonic()
    with _RESPONSE_CACHE_LOCK:
        entry = _RESPONSE_CACHE.get(cache_key)
        if entry is None:
            return None
        if now - entry.built_at > MEMORY_CACHE_TTL_SECONDS or entry.request_digest != request_digest:
            _RESPONSE_CACHE.pop(cache_key, None)
            return None
        return copy.deepcopy(entry.response)


def store_cached_response(request: AnalysisRequest, response: AnalysisResponse) -> None:
    cache_key = _position_cache_key(request)
    request_digest = _request_digest(request)
    entry = _ResponseCacheEntry(
        built_at=time.monotonic(),
        response=copy.deepcopy(response),
        request_digest=request_digest,
    )
    with _RESPONSE_CACHE_LOCK:
        if len(_RESPONSE_CACHE) >= MEMORY_CACHE_MAX_ENTRIES:
            oldest_key = min(_RESPONSE_CACHE.items(), key=lambda item: item[1].built_at)[0]
            _RESPONSE_CACHE.pop(oldest_key, None)
        _RESPONSE_CACHE[cache_key] = entry

    redis_client = _redis_client()
    if redis_client is not None:
        redis_client.set(cache_key, request_digest, response)


class _RedisCacheClient:
    def __init__(self, host: str, port: int, timeout_seconds: float = 0.5) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def get(self, cache_key: str, request_digest: str) -> AnalysisResponse | None:
        try:
            with self._lock:
                payload = self._command("GET", cache_key)
                if payload is None:
                    return None
                if not isinstance(payload, (bytes, str)):
                    return None
                raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
                parsed = json.loads(raw)
                if not isinstance(parsed, dict) or parsed.get("request_digest") != request_digest:
                    return None
                response_payload = parsed.get("response")
                if not isinstance(response_payload, dict):
                    return None
                self._command("ZADD", REDIS_INDEX_KEY, str(time.time()), cache_key)
                return _response_from_dict(response_payload)
        except Exception:
            return None

    def set(self, cache_key: str, request_digest: str, response: AnalysisResponse) -> None:
        payload = json.dumps(
            {
                "request_digest": request_digest,
                "response": response.to_dict(),
            },
            ensure_ascii=False,
        )
        try:
            with self._lock:
                self._command("SETEX", cache_key, str(REDIS_CACHE_TTL_SECONDS), payload)
                now = str(time.time())
                self._command("ZADD", REDIS_INDEX_KEY, now, cache_key)
                size = self._command("ZCARD", REDIS_INDEX_KEY)
                if isinstance(size, int) and size > REDIS_CACHE_MAX_ENTRIES:
                    overflow = size - REDIS_CACHE_MAX_ENTRIES
                    oldest = self._command("ZRANGE", REDIS_INDEX_KEY, "0", str(overflow - 1))
                    if isinstance(oldest, list):
                        for key in oldest:
                            text_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                            self._command("DEL", text_key)
                            self._command("ZREM", REDIS_INDEX_KEY, text_key)
        except Exception:
            return

    def _command(self, *parts: str) -> Any:
        with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as conn:
            conn.sendall(_encode_resp(parts))
            file = conn.makefile("rb")
            return _read_resp(file)


def _redis_client() -> _RedisCacheClient | None:
    global _REDIS_CLIENT
    with _REDIS_CLIENT_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        if not _redis_enabled():
            return None
        client = _RedisCacheClient(
            host=os.environ.get("LEGACYLENS_REDIS_HOST", REDIS_DEFAULT_HOST),
            port=_redis_port(),
        )
        try:
            with client._lock:
                pong = client._command("PING")
            if pong not in {b"PONG", "PONG"}:
                return None
        except Exception:
            return None
        _REDIS_CLIENT = client
        return _REDIS_CLIENT


def _redis_enabled() -> bool:
    configured = os.environ.get("LEGACYLENS_REDIS_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return True


def _redis_port() -> int:
    try:
        return int(os.environ.get("LEGACYLENS_REDIS_PORT", str(REDIS_DEFAULT_PORT)))
    except ValueError:
        return REDIS_DEFAULT_PORT


def _position_cache_key(request: AnalysisRequest) -> str:
    file_name = _resolve_file_key(request.file_name) or "<memory>"
    normalized_line, normalized_column = _normalized_position(request)
    digest = hashlib.sha256()
    digest.update(file_name.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update((request.language or "").encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update((request.output_language or "").encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update((request.ui_language or "").encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(request.context_scope.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(str(int(request.use_llm)).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(normalized_line).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(normalized_column).encode("ascii"))
    return f"{REDIS_KEY_PREFIX}{digest.hexdigest()}"


def _request_digest(request: AnalysisRequest) -> str:
    digest = hashlib.sha256()
    digest.update((request.project_root or "").encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(str(request.excerpt_start_line).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(request.max_findings).encode("ascii"))
    digest.update(b"\0")
    digest.update(request.code.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _resolve_file_key(file_name: str | None) -> str | None:
    if not file_name:
        return None
    path = Path(file_name)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _normalized_position(request: AnalysisRequest) -> tuple[int, int]:
    line_number = request.cursor_line or 0
    column_number = request.cursor_column or 0
    relative_line = request.relative_cursor_line()
    if relative_line is None:
        return line_number, column_number
    lines = request.code.splitlines()
    if not (1 <= relative_line <= len(lines)):
        return line_number, column_number
    line = lines[relative_line - 1]
    if not line:
        return line_number, column_number
    zero_based_column = max(0, min(len(line) - 1, (request.cursor_column or 1) - 1))
    for match in IDENTIFIER_PATTERN.finditer(line):
        if match.start() <= zero_based_column < match.end():
            return line_number, match.start() + 1
    return line_number, column_number


def _encode_resp(parts: tuple[str, ...]) -> bytes:
    encoded: list[bytes] = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        data = part.encode("utf-8")
        encoded.append(f"${len(data)}\r\n".encode("ascii"))
        encoded.append(data + b"\r\n")
    return b"".join(encoded)


def _read_resp(file) -> Any:
    prefix = file.read(1)
    if not prefix:
        raise OSError("empty redis response")
    if prefix == b"+":
        return file.readline().rstrip(b"\r\n")
    if prefix == b"-":
        raise OSError(file.readline().decode("utf-8", errors="replace").strip())
    if prefix == b":":
        return int(file.readline().strip() or b"0")
    if prefix == b"$":
        length = int(file.readline().strip() or b"-1")
        if length < 0:
            return None
        data = file.read(length)
        file.read(2)
        return data
    if prefix == b"*":
        count = int(file.readline().strip() or b"0")
        if count < 0:
            return None
        return [_read_resp(file) for _ in range(count)]
    raise OSError(f"unsupported redis response prefix: {prefix!r}")


def _response_from_dict(payload: dict[str, Any]) -> AnalysisResponse:
    findings: list[Finding] = []
    for item in payload.get("findings", []):
        if not isinstance(item, dict):
            continue
        span_payload = item.get("span") if isinstance(item.get("span"), dict) else {}
        severity_text = str(item.get("severity", "info")).lower()
        try:
            severity = Severity(severity_text)
        except ValueError:
            severity = Severity.INFO
        findings.append(
            Finding(
                rule_id=str(item.get("rule_id", "")),
                language=str(item.get("language", "")),
                title=str(item.get("title", "")),
                severity=severity,
                span=SourceSpan(
                    start_line=int(span_payload.get("start_line", 0) or 0),
                    end_line=int(span_payload.get("end_line", 0) or 0),
                    text=str(span_payload.get("text", "")),
                ),
                rationale=str(item.get("rationale", "")),
                historical_context=str(item.get("historical_context", "")),
                remediation_hint=item.get("remediation_hint"),
                tags=tuple(str(tag) for tag in item.get("tags", []) if tag is not None),
                confidence=float(item.get("confidence", 0.75) or 0.75),
            )
        )

    context_payload = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context = None
    if context_payload is not None:
        context = ProjectContext(
            scope=str(context_payload.get("scope", "none")),
            root=context_payload.get("root"),
            current_directory=context_payload.get("current_directory"),
            current_file=context_payload.get("current_file"),
            files=[str(item) for item in context_payload.get("files", []) if item is not None],
            related_files=[str(item) for item in context_payload.get("related_files", []) if item is not None],
            symbol_references=[
                item for item in context_payload.get("symbol_references", []) if isinstance(item, dict)
            ],
            notes=[str(item) for item in context_payload.get("notes", []) if item is not None],
        )

    return AnalysisResponse(
        language=str(payload.get("language", "")),
        findings=findings,
        context=context,
        markdown=str(payload.get("markdown", "")),
        model_used=payload.get("model_used"),
        fallback_reason=payload.get("fallback_reason"),
        output_language=payload.get("output_language"),
    )
