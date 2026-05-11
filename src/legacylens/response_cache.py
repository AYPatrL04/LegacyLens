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
from typing import Any, BinaryIO

from .config import first_string, load_config_payload_or_empty, mapping
from .models import AnalysisRequest, AnalysisResponse, Finding, ProjectContext, Severity, SourceSpan

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MEMORY_CACHE_TTL_SECONDS = 180.0
MEMORY_CACHE_MAX_ENTRIES = 256
REDIS_CACHE_TTL_SECONDS = 21_600
REDIS_CACHE_MAX_ENTRIES = 2_048
REDIS_DEFAULT_HOST = "127.0.0.1"
REDIS_DEFAULT_PORT = 6379
REDIS_KEY_PREFIX = "legacylens:hover:"
REDIS_INDEX_KEY = f"{REDIS_KEY_PREFIX}index"


@dataclass(frozen=True)
class CacheKey:
    file_name: str
    language: str
    output_language: str
    ui_language: str
    context_scope: str
    use_llm: bool
    cursor_line: int
    cursor_column: int

    def redis_key(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.file_name.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(self.language.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(self.output_language.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(self.ui_language.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(self.context_scope.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(int(self.use_llm)).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(self.cursor_line).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(self.cursor_column).encode("ascii"))
        return f"{REDIS_KEY_PREFIX}{digest.hexdigest()}"


@dataclass(frozen=True)
class CacheEntry:
    built_at: float
    request_digest: str
    response: AnalysisResponse


class MemoryResponseCache:
    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, cache_key: str, request_digest: str) -> AnalysisResponse | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is None:
                return None
            if now - entry.built_at > self.ttl_seconds or entry.request_digest != request_digest:
                self._entries.pop(cache_key, None)
                return None
            return copy.deepcopy(entry.response)

    def set(self, cache_key: str, request_digest: str, response: AnalysisResponse) -> None:
        entry = CacheEntry(
            built_at=time.monotonic(),
            request_digest=request_digest,
            response=copy.deepcopy(response),
        )
        with self._lock:
            if len(self._entries) >= self.max_entries:
                oldest_key = min(self._entries.items(), key=lambda item: item[1].built_at)[0]
                self._entries.pop(oldest_key, None)
            self._entries[cache_key] = entry


class RedisResponseCache:
    def __init__(self, host: str, port: int, timeout_seconds: float = 0.5) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def ping(self) -> bool:
        try:
            with self._lock:
                return self._command("PING") in {b"PONG", "PONG"}
        except Exception:
            return False

    def get(self, cache_key: str, request_digest: str) -> AnalysisResponse | None:
        try:
            with self._lock:
                payload = self._command("GET", cache_key)
                if payload is None:
                    return None
                raw = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
                parsed = json.loads(raw)
                if not isinstance(parsed, dict) or parsed.get("request_digest") != request_digest:
                    return None
                self._touch(cache_key)
                response_payload = parsed.get("response")
                if not isinstance(response_payload, dict):
                    return None
                return _response_from_dict(response_payload)
        except Exception:
            return None

    def set(self, cache_key: str, request_digest: str, response: AnalysisResponse) -> None:
        payload = json.dumps({"request_digest": request_digest, "response": response.to_dict()}, ensure_ascii=False)
        try:
            with self._lock:
                self._command("SETEX", cache_key, str(REDIS_CACHE_TTL_SECONDS), payload)
                self._touch(cache_key)
                self._trim()
        except Exception:
            return

    def _touch(self, cache_key: str) -> None:
        self._command("ZADD", REDIS_INDEX_KEY, str(time.time()), cache_key)

    def _trim(self) -> None:
        size = self._command("ZCARD", REDIS_INDEX_KEY)
        if not isinstance(size, int) or size <= REDIS_CACHE_MAX_ENTRIES:
            return
        overflow = size - REDIS_CACHE_MAX_ENTRIES
        stale_keys = self._command("ZRANGE", REDIS_INDEX_KEY, "0", str(overflow - 1))
        if not isinstance(stale_keys, list):
            return
        for key in stale_keys:
            text_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            self._command("DEL", text_key)
            self._command("ZREM", REDIS_INDEX_KEY, text_key)

    def _command(self, *parts: str) -> Any:
        with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as conn:
            conn.sendall(_encode_resp(parts))
            file = conn.makefile("rb")
            return _read_resp(file)


class ResponseCacheStore:
    def __init__(self) -> None:
        self._memory = MemoryResponseCache(MEMORY_CACHE_TTL_SECONDS, MEMORY_CACHE_MAX_ENTRIES)
        self._redis: RedisResponseCache | None = None
        self._redis_checked = False
        self._redis_lock = threading.Lock()

    def load(self, request: AnalysisRequest) -> AnalysisResponse | None:
        cache_key = _build_cache_key(request)
        redis_key = cache_key.redis_key()
        request_digest = _request_digest(request)
        redis_cache = self._redis_cache()
        if redis_cache is not None:
            cached = redis_cache.get(redis_key, request_digest)
            if cached is not None:
                return cached
        return self._memory.get(redis_key, request_digest)

    def store(self, request: AnalysisRequest, response: AnalysisResponse) -> None:
        cache_key = _build_cache_key(request)
        redis_key = cache_key.redis_key()
        request_digest = _request_digest(request)
        self._memory.set(redis_key, request_digest, response)
        redis_cache = self._redis_cache()
        if redis_cache is not None:
            redis_cache.set(redis_key, request_digest, response)

    def _redis_cache(self) -> RedisResponseCache | None:
        with self._redis_lock:
            if self._redis_checked:
                return self._redis
            self._redis_checked = True
            config = _redis_config()
            if not config.enabled:
                return None
            candidate = RedisResponseCache(
                host=config.host,
                port=config.port,
            )
            if not candidate.ping():
                return None
            self._redis = candidate
            return self._redis


_CACHE = ResponseCacheStore()


def load_cached_response(request: AnalysisRequest) -> AnalysisResponse | None:
    return _CACHE.load(request)


def store_cached_response(request: AnalysisRequest, response: AnalysisResponse) -> None:
    _CACHE.store(request, response)


def _build_cache_key(request: AnalysisRequest) -> CacheKey:
    line_number, column_number = _normalized_position(request)
    return CacheKey(
        file_name=_resolve_file_key(request.file_name) or "<memory>",
        language=request.language or "",
        output_language=request.output_language or "",
        ui_language=request.ui_language or "",
        context_scope=request.context_scope,
        use_llm=request.use_llm,
        cursor_line=line_number,
        cursor_column=column_number,
    )


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


def _redis_enabled() -> bool:
    return _redis_config().enabled


@dataclass(frozen=True)
class RedisConfig:
    enabled: bool
    host: str
    port: int


def _redis_config() -> RedisConfig:
    payload, _ = load_config_payload_or_empty()
    cache_config = mapping(payload.get("cache"))
    redis_config = mapping(cache_config.get("redis")) or mapping(payload.get("redis"))

    enabled_text = first_string(os.environ.get("LEGACYLENS_REDIS_ENABLED"), redis_config.get("enabled"))
    if enabled_text is None:
        enabled = True
    else:
        enabled = str(enabled_text).strip().lower() in {"1", "true", "yes", "on"}

    host = first_string(os.environ.get("LEGACYLENS_REDIS_HOST"), redis_config.get("host"), REDIS_DEFAULT_HOST) or REDIS_DEFAULT_HOST
    port = _int_value(os.environ.get("LEGACYLENS_REDIS_PORT"), redis_config.get("port"), default=REDIS_DEFAULT_PORT)
    return RedisConfig(enabled=enabled, host=host, port=port)


def _int_value(*values: Any, default: int) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _encode_resp(parts: tuple[str, ...]) -> bytes:
    encoded: list[bytes] = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        data = part.encode("utf-8")
        encoded.append(f"${len(data)}\r\n".encode("ascii"))
        encoded.append(data + b"\r\n")
    return b"".join(encoded)


def _read_resp(file: BinaryIO) -> Any:
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
            symbol_references=[item for item in context_payload.get("symbol_references", []) if isinstance(item, dict)],
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
