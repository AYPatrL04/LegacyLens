from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import time
from functools import lru_cache
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from .config import find_config_path, load_config_payload
from .i18n import ENGLISH, OutputLanguage, resolve_output_language
from .models import AnalysisRequest, Finding, ProjectContext

DEFAULT_MODEL_PREFERENCES = (
    "qwen",
    "deepseek",
    "codellama",
    "codegemma",
    "llama",
    "mistral",
)
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_API_PATH = "/chat/completions"
LOGGER = logging.getLogger("legacylens.llm")
HTTP_LIMITS = httpx.Limits(max_keepalive_connections=8, max_connections=16)


@dataclass(frozen=True)
class Explanation:
    markdown: str
    model_used: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class SectionPrompt:
    index: int
    heading: str
    prompt: str


@dataclass(frozen=True)
class LlmConfig:
    mode: str = "local"
    config_path: str | None = None
    timeout_seconds: float = 60.0
    parallel_sections: bool = False
    parallel_section_limit: int = 4
    model: str | None = None
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str | None = None
    ollama_prefer: tuple[str, ...] = DEFAULT_MODEL_PREFERENCES
    ollama_disable_autodiscovery: bool = False
    api_url: str | None = None
    api_base_url: str | None = None
    api_path: str = DEFAULT_API_PATH
    api_key: str | None = None
    api_key_env: str | None = None
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    api_model: str | None = None
    api_headers: dict[str, str] = field(default_factory=dict)


class LlmClient(Protocol):
    provider: str
    model: str | None
    host: str

    def generate(self, prompt: str) -> str:
        ...

    def generate_stream(self, prompt: str) -> Iterator[str]:
        ...


class Explainer:
    def __init__(self, client: LlmClient | None = None) -> None:
        self.client = client
        self._client_checked = client is not None
        self._client_error: str | None = None

    def explain(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        context: ProjectContext | None = None,
    ) -> Explanation:
        output_language = resolve_output_language(request.output_language, request.ui_language)
        prompt = _build_prompt(request, language, findings, context, output_language)
        if not request.use_llm:
            LOGGER.info("llm disabled; using deterministic fallback language=%s file=%s", language, request.file_name or "unknown")
            return Explanation(markdown=_render_deterministic(language, findings, context, request, output_language))

        self._ensure_client()

        if self.client is not None:
            started_at = time.monotonic()
            _log_llm_start(self.client, stream=False, prompt=prompt, language=language, request=request, output_language=output_language)
            try:
                if _parallel_sections_enabled():
                    markdown = asyncio.run(
                        self._generate_parallel_markdown(
                            request,
                            language,
                            findings,
                            context,
                            output_language,
                        )
                    )
                else:
                    markdown = self.client.generate(prompt)
                if markdown:
                    _log_llm_success(self.client, stream=False, started_at=started_at, output_chars=len(markdown))
                    return Explanation(
                        markdown=_append_line_reference_warning(markdown, request, findings, context, output_language),
                        model_used=self.client.model,
                    )
                reason = _empty_response_reason(self.client)
                _log_llm_fallback(self.client, reason, stream=False, started_at=started_at)
                return Explanation(
                    markdown=_render_deterministic(language, findings, context, request, output_language),
                    fallback_reason=reason,
                )
            except OSError as exc:
                reason = _unavailable_reason(self.client, exc)
                _log_llm_failure(self.client, exc, stream=False, started_at=started_at)
                _log_llm_fallback(self.client, reason, stream=False, started_at=started_at)
                return Explanation(
                    markdown=_render_deterministic(language, findings, context, request, output_language),
                    fallback_reason=reason,
                )

        reason = self._no_client_reason()
        LOGGER.warning("llm unavailable before call; using deterministic fallback reason=%s language=%s file=%s", reason, language, request.file_name or "unknown")
        return Explanation(
            markdown=_render_deterministic(language, findings, context, request, output_language),
            fallback_reason=reason,
        )

    def explain_stream(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        context: ProjectContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        output_language = resolve_output_language(request.output_language, request.ui_language)
        prompt = _build_prompt(request, language, findings, context, output_language)
        if not request.use_llm:
            LOGGER.info("llm disabled for stream; using deterministic fallback language=%s file=%s", language, request.file_name or "unknown")
            markdown = _render_deterministic(language, findings, context, request, output_language)
            yield {
                "type": "delta",
                "text": markdown,
            }
            yield {"type": "done", "model_used": None, "fallback_reason": None}
            return

        self._ensure_client()
        if self.client is None:
            reason = self._no_client_reason()
            LOGGER.warning("llm unavailable before stream; using deterministic fallback reason=%s language=%s file=%s", reason, language, request.file_name or "unknown")
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, context, request, output_language),
            }
            yield {"type": "done", "model_used": None, "fallback_reason": reason}
            return

        emitted = False
        chunks: list[str] = []
        started_at = time.monotonic()
        _log_llm_start(self.client, stream=True, prompt=prompt, language=language, request=request, output_language=output_language)
        try:
            if _parallel_sections_enabled():
                for text in self._parallel_stream_sections(request, language, findings, context, output_language):
                    emitted = True
                    chunks.append(text)
                    yield {"type": "delta", "text": text}
            else:
                for text in self.client.generate_stream(prompt):
                    emitted = True
                    chunks.append(text)
                    yield {"type": "delta", "text": text}
        except OSError as exc:
            reason = _unavailable_reason(self.client, exc)
            _log_llm_failure(self.client, exc, stream=True, started_at=started_at)
            _log_llm_fallback(self.client, reason, stream=True, started_at=started_at)
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, context, request, output_language),
            }
            yield {"type": "done", "model_used": self.client.model, "fallback_reason": reason}
            return

        if not emitted:
            reason = _empty_response_reason(self.client)
            _log_llm_fallback(self.client, reason, stream=True, started_at=started_at)
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, context, request, output_language),
            }
            yield {"type": "done", "model_used": self.client.model, "fallback_reason": reason}
            return

        _log_llm_success(self.client, stream=True, started_at=started_at, output_chars=sum(len(chunk) for chunk in chunks))
        warning = _line_reference_warning("".join(chunks), request, findings, context, output_language)
        if warning:
            LOGGER.warning("llm line-reference warning provider=%s model=%s reason=%s", self.client.provider, _display_model(self.client.model), warning)
            yield {"type": "delta", "text": f"\n\n> {_line_warning_label(output_language)}: {warning}"}
        yield {"type": "done", "model_used": self.client.model, "fallback_reason": None}

    def model_status(self) -> dict[str, str | bool | None]:
        self._ensure_client()
        try:
            config = load_llm_config()
        except ValueError:
            config = LlmConfig()
        return {
            "available": self.client is not None,
            "provider": getattr(self.client, "provider", config.mode) if self.client else config.mode,
            "mode": config.mode,
            "model": getattr(self.client, "model", None) if self.client else _configured_model(config),
            "host": getattr(self.client, "host", None) if self.client else _configured_host(config),
            "config_path": config.config_path,
        }

    def _ensure_client(self) -> None:
        if self.client is None and not self._client_checked:
            try:
                self.client = client_from_configuration()
            except ValueError as exc:
                self.client = None
                self._client_error = str(exc)
                LOGGER.warning("llm client configuration failed reason=%s", exc)
            if self.client is not None:
                LOGGER.info(
                    "llm client configured provider=%s model=%s host=%s",
                    self.client.provider,
                    _display_model(self.client.model),
                    _safe_host(self.client.host),
                )
            else:
                LOGGER.warning("llm client not configured reason=%s", self._no_client_reason())
            self._client_checked = True

    def _no_client_reason(self) -> str:
        if self._client_error:
            return self._client_error
        try:
            config = load_llm_config()
        except ValueError as exc:
            return str(exc)
        if config.mode == "api":
            if not _resolve_api_url(config.api_url, config.api_base_url, config.api_path):
                return "API mode is configured but no api.url or api.baseUrl was provided."
            return "API mode is configured but no API client could be created."
        return "Local Ollama model was not configured or auto-discovered."

    async def _generate_parallel_markdown(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        context: ProjectContext | None,
        output_language: OutputLanguage,
    ) -> str:
        if self.client is None:
            return ""
        prompts = _build_section_prompts(request, language, findings, context, output_language)
        semaphore = asyncio.Semaphore(_parallel_section_limit())

        async def generate_section(spec: SectionPrompt) -> tuple[int, str]:
            async with semaphore:
                text = await asyncio.to_thread(self.client.generate, spec.prompt)
            return spec.index, _normalize_section_markdown(spec.heading, text)

        results = await asyncio.gather(*(generate_section(spec) for spec in prompts))
        ordered = [text for _, text in sorted(results, key=lambda item: item[0]) if text.strip()]
        if len(ordered) == len(prompts):
            return "\n\n".join(ordered)
        return self.client.generate(_build_prompt(request, language, findings, context, output_language))

    def _parallel_stream_sections(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        context: ProjectContext | None,
        output_language: OutputLanguage,
    ) -> Iterator[str]:
        if self.client is None:
            return iter(())

        async def collect_sections() -> list[str]:
            prompts = _build_section_prompts(request, language, findings, context, output_language)
            semaphore = asyncio.Semaphore(_parallel_section_limit())
            section_results: dict[int, str] = {}

            async def generate_section(spec: SectionPrompt) -> tuple[int, str]:
                async with semaphore:
                    text = await asyncio.to_thread(self.client.generate, spec.prompt)
                return spec.index, _normalize_section_markdown(spec.heading, text)

            by_index: dict[int, str] = {}
            ordered: list[str] = []
            next_index = 0
            for completed in asyncio.as_completed([generate_section(spec) for spec in prompts]):
                index, text = await completed
                section_results[index] = text
                by_index[index] = text
                while next_index in by_index:
                    section_text = by_index.pop(next_index)
                    if section_text.strip():
                        if ordered:
                            ordered.append("\n\n")
                        ordered.append(section_text)
                    next_index += 1
            if next_index != len(prompts) or any(not section_results.get(spec.index, "").strip() for spec in prompts):
                fallback = await asyncio.to_thread(
                    self.client.generate,
                    _build_prompt(request, language, findings, context, output_language),
                )
                return [fallback]
            return ordered

        for chunk in asyncio.run(collect_sections()):
            yield chunk


@dataclass(frozen=True)
class OllamaClient:
    host: str
    model: str
    timeout_seconds: float = 60.0
    provider: str = field(default="ollama", init=False)

    @classmethod
    def from_environment(cls) -> "OllamaClient | None":
        config = load_llm_config()
        if config.mode != "local":
            return None
        return cls.from_config(config)

    @classmethod
    def from_config(cls, config: LlmConfig) -> "OllamaClient | None":
        host = normalize_ollama_host(config.ollama_host)
        model = config.ollama_model or config.model
        if not model and not config.ollama_disable_autodiscovery:
            try:
                model = discover_ollama_model(host, preferences=config.ollama_prefer)
            except OSError:
                model = None
        if not model:
            return None
        return cls(host=host, model=model, timeout_seconds=config.timeout_seconds)

    @classmethod
    def discover(cls, host: str | None = None) -> "OllamaClient | None":
        resolved_host = normalize_ollama_host(host or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
        model = discover_ollama_model(resolved_host)
        if not model:
            return None
        return cls(host=resolved_host, model=model)

    def generate(self, prompt: str) -> str:
        data = _post_json(
            f"{self.host}/api/generate",
            payload={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 700},
            },
            timeout_seconds=self.timeout_seconds,
        )
        raw = str(data.get("response", "")).strip()
        cleaned = _strip_thinking(raw)
        return cleaned or raw

    def generate_stream(self, prompt: str) -> Iterator[str]:
        for line in _stream_lines(
            f"{self.host}/api/generate",
            payload={
                "model": self.model,
                "prompt": prompt,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 700},
            },
            timeout_seconds=self.timeout_seconds,
        ):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk = str(data.get("response", ""))
            if chunk:
                yield chunk
            if data.get("done"):
                break


@dataclass(frozen=True)
class ApiClient:
    url: str
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: float = 60.0
    headers: dict[str, str] = field(default_factory=dict)
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    provider: str = field(default="api", init=False)

    @property
    def host(self) -> str:
        return self.url

    @classmethod
    def from_config(cls, config: LlmConfig) -> "ApiClient | None":
        url = _resolve_api_url(config.api_url, config.api_base_url, config.api_path)
        if not url:
            return None
        return cls(
            url=url,
            api_key=config.api_key,
            model=config.api_model or config.model,
            timeout_seconds=config.timeout_seconds,
            headers=config.api_headers,
            api_key_header=config.api_key_header,
            api_key_prefix=config.api_key_prefix,
        )

    def generate(self, prompt: str) -> str:
        data = _post_json(
            self.url,
            payload=self._payload(prompt, stream=False),
            headers=self._headers(),
            timeout_seconds=self.timeout_seconds,
        )
        raw = _extract_api_content(data).strip()
        cleaned = _strip_thinking(raw)
        return cleaned or raw

    def generate_stream(self, prompt: str) -> Iterator[str]:
        for line in _stream_lines(
            self.url,
            payload=self._payload(prompt, stream=True),
            headers=self._headers(),
            timeout_seconds=self.timeout_seconds,
        ):
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk = _extract_api_delta(data)
            if chunk:
                yield chunk

    def _payload(self, prompt: str, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "stream": stream,
        }
        if self.model:
            payload["model"] = self.model
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.headers)
        if self.api_key:
            headers[self.api_key_header] = f"{self.api_key_prefix}{self.api_key}" if self.api_key_prefix else self.api_key
        return headers


def client_from_configuration(config: LlmConfig | None = None) -> LlmClient | None:
    resolved = config or load_llm_config()
    if resolved.mode == "api":
        return ApiClient.from_config(resolved)
    return OllamaClient.from_config(resolved)


def discover_ollama_model(host: str = DEFAULT_OLLAMA_HOST, preferences: tuple[str, ...] | None = None) -> str | None:
    models = list_ollama_models(host=host)
    return select_preferred_model(models, preferences=preferences)


@lru_cache(maxsize=16)
def list_ollama_models(host: str = DEFAULT_OLLAMA_HOST, timeout_seconds: float = 2.0) -> list[str]:
    resolved_host = normalize_ollama_host(host)
    data = _get_json(f"{resolved_host}/api/tags", timeout_seconds=timeout_seconds)

    models = data.get("models", [])
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get("name"), str):
            names.append(model["name"])
    return names


def select_preferred_model(models: list[str], preferences: tuple[str, ...] | None = None) -> str | None:
    if not models:
        return None
    preferred = [item.strip().lower() for item in (preferences or _preference_list(
        os.environ.get("LEGACYLENS_OLLAMA_PREFER"),
        default=DEFAULT_MODEL_PREFERENCES,
    )) if item.strip()]
    lower_models = [(model.lower(), model) for model in models]
    for prefix in preferred:
        for lower, original in lower_models:
            if lower.startswith(prefix) or prefix in lower:
                return original
    return models[0]


def normalize_ollama_host(host: str | None) -> str:
    cleaned = (host or DEFAULT_OLLAMA_HOST).strip().rstrip("/")
    if cleaned.startswith(":"):
        return f"http://127.0.0.1{cleaned}"
    if "://" not in cleaned:
        return f"http://{cleaned}"
    return cleaned


def load_llm_config() -> LlmConfig:
    payload, config_path = load_config_payload()
    llm = _mapping(payload.get("llm")) or payload
    analysis = _mapping(payload.get("analysis"))
    local = _mapping(llm.get("local")) or _mapping(llm.get("ollama")) or {}
    api = _mapping(llm.get("api")) or {}

    mode = _normalize_mode(_first_string(os.environ.get("LEGACYLENS_LLM_MODE"), llm.get("mode"), llm.get("provider")))
    timeout = _float_value(
        os.environ.get("LEGACYLENS_LLM_TIMEOUT"),
        os.environ.get("LEGACYLENS_OLLAMA_TIMEOUT"),
        os.environ.get("LEGACYLENS_API_TIMEOUT"),
        llm.get("timeoutSeconds"),
        llm.get("timeout_seconds"),
        default=60.0,
    )
    model = _first_string(os.environ.get("LEGACYLENS_MODEL"), llm.get("model"))
    api_key_env = _first_string(os.environ.get("LEGACYLENS_API_KEY_ENV"), api.get("apiKeyEnv"), api.get("keyEnv"))
    api_key = _first_string(
        os.environ.get("LEGACYLENS_API_KEY"),
        os.environ.get(api_key_env) if api_key_env else None,
        api.get("apiKey"),
        api.get("key"),
    )

    return LlmConfig(
        mode=mode,
        config_path=str(config_path) if config_path else None,
        timeout_seconds=timeout,
        parallel_sections=_bool_value(
            os.environ.get("LEGACYLENS_LLM_PARALLEL_SECTIONS"),
            llm.get("parallelSections"),
            llm.get("parallel_sections"),
            analysis.get("parallelSections"),
            analysis.get("parallel_sections"),
            default=False,
        ),
        parallel_section_limit=max(
            1,
            int(
                _float_value(
                    os.environ.get("LEGACYLENS_LLM_PARALLEL_SECTION_LIMIT"),
                    llm.get("parallelSectionLimit"),
                    llm.get("parallel_section_limit"),
                    analysis.get("parallelSectionLimit"),
                    analysis.get("parallel_section_limit"),
                    default=4.0,
                )
            ),
        ),
        model=model,
        ollama_host=normalize_ollama_host(_first_string(os.environ.get("OLLAMA_HOST"), local.get("host"), llm.get("ollamaHost"), DEFAULT_OLLAMA_HOST)),
        ollama_model=_first_string(os.environ.get("LEGACYLENS_OLLAMA_MODEL"), os.environ.get("OLLAMA_MODEL"), local.get("model"), model if mode == "local" else None),
        ollama_prefer=_preference_list(
            os.environ.get("LEGACYLENS_OLLAMA_PREFER"),
            local.get("prefer"),
            local.get("preferences"),
            default=DEFAULT_MODEL_PREFERENCES,
        ),
        ollama_disable_autodiscovery=_bool_value(
            os.environ.get("LEGACYLENS_DISABLE_OLLAMA_AUTODISCOVERY"),
            local.get("disableAutodiscovery"),
            local.get("disable_autodiscovery"),
            default=False,
        ),
        api_url=_first_string(os.environ.get("LEGACYLENS_API_URL"), api.get("url")),
        api_base_url=_first_string(os.environ.get("LEGACYLENS_API_BASE_URL"), api.get("baseUrl"), api.get("base_url")),
        api_path=_first_string(os.environ.get("LEGACYLENS_API_PATH"), api.get("path"), api.get("endpoint"), DEFAULT_API_PATH) or DEFAULT_API_PATH,
        api_key=api_key,
        api_key_env=api_key_env,
        api_key_header=_first_string(os.environ.get("LEGACYLENS_API_KEY_HEADER"), api.get("apiKeyHeader"), api.get("keyHeader"), "Authorization") or "Authorization",
        api_key_prefix=_first_raw_string(os.environ.get("LEGACYLENS_API_KEY_PREFIX"), api.get("apiKeyPrefix"), api.get("keyPrefix"), "Bearer "),
        api_model=_first_string(os.environ.get("LEGACYLENS_API_MODEL"), api.get("model"), model if mode == "api" else None),
        api_headers=_string_mapping(api.get("headers")),
    )

def _resolve_api_url(api_url: str | None, api_base_url: str | None, api_path: str | None) -> str | None:
    if api_url:
        return api_url.strip()
    if not api_base_url:
        return None
    base = api_base_url.strip().rstrip("/")
    path = (api_path or DEFAULT_API_PATH).strip()
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _configured_host(config: LlmConfig) -> str | None:
    if config.mode == "api":
        return _resolve_api_url(config.api_url, config.api_base_url, config.api_path)
    return config.ollama_host


def _configured_model(config: LlmConfig) -> str | None:
    if config.mode == "api":
        return config.api_model or config.model
    return config.ollama_model or config.model


@lru_cache(maxsize=16)
def _client_for_origin(scheme: str, netloc: str, timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_seconds,
        limits=HTTP_LIMITS,
        follow_redirects=True,
        http2=False,
    )


@lru_cache(maxsize=64)
def _prepared_request_target(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or _is_ip_address(parts.hostname):
        return url, ()

    resolved_ip = _resolve_hostname(parts.hostname)
    if not resolved_ip:
        return url, ()

    port = parts.port
    host_header = parts.hostname if port in {None, 80} else f"{parts.hostname}:{port}"
    replacement_host = _format_host_for_netloc(resolved_ip)
    if port is not None:
        replacement_host = f"{replacement_host}:{port}"
    replaced = SplitResult(
        scheme=parts.scheme,
        netloc=replacement_host,
        path=parts.path,
        query=parts.query,
        fragment=parts.fragment,
    )
    return urlunsplit(replaced), (("Host", host_header),)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout_seconds: float = 60.0) -> dict[str, Any]:
    response = _send_request(
        "POST",
        url,
        json_payload=payload,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    return _json_response(response)


def _get_json(url: str, headers: dict[str, str] | None = None, timeout_seconds: float = 60.0) -> dict[str, Any]:
    response = _send_request(
        "GET",
        url,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    return _json_response(response)


def _stream_lines(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout_seconds: float = 60.0) -> Iterator[str]:
    request_url, resolved_headers = _prepared_request_target(url)
    merged_headers = _merge_headers(headers, resolved_headers)
    client = _client_for_origin(urlsplit(url).scheme, urlsplit(url).netloc, timeout_seconds)
    try:
        with client.stream("POST", request_url, json=payload, headers=merged_headers, timeout=timeout_seconds) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                text = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                stripped = text.strip()
                if stripped:
                    yield stripped
    except httpx.HTTPStatusError as exc:
        raise OSError(_http_error_message(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError(str(exc)) from exc


def _send_request(
    method: str,
    url: str,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 60.0,
) -> httpx.Response:
    request_url, resolved_headers = _prepared_request_target(url)
    merged_headers = _merge_headers(headers, resolved_headers)
    client = _client_for_origin(urlsplit(url).scheme, urlsplit(url).netloc, timeout_seconds)
    try:
        response = client.request(method, request_url, json=json_payload, headers=merged_headers, timeout=timeout_seconds)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise OSError(_http_error_message(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError(str(exc)) from exc


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    return data if isinstance(data, dict) else {}


def _extract_api_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = _content_to_text(message.get("content"))
                if content:
                    return content
            text = _content_to_text(choice.get("text"))
            if text:
                return text
    for key in ("response", "content", "text", "output"):
        text = _content_to_text(data.get(key))
        if text:
            return text
    return ""


def _extract_api_delta(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = _content_to_text(delta.get("content"))
                if content:
                    return content
            text = _content_to_text(choice.get("text"))
            if text:
                return text
    for key in ("response", "content", "text", "output"):
        text = _content_to_text(data.get(key))
        if text:
            return text
    return ""


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    body = exc.response.text.strip()[:500]
    if body:
        return f"HTTP {exc.response.status_code}: {body}"
    return f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"


def _empty_response_reason(client: LlmClient) -> str:
    model = f" model {client.model}" if client.model else ""
    return f"{client.provider}{model} returned an empty response."


def _unavailable_reason(client: LlmClient, exc: OSError) -> str:
    return f"{client.provider} unavailable: {exc}"


def _log_llm_start(
    client: LlmClient,
    stream: bool,
    prompt: str,
    language: str,
    request: AnalysisRequest,
    output_language: OutputLanguage,
) -> None:
    LOGGER.info(
        "llm call start provider=%s model=%s host=%s stream=%s prompt_chars=%d language=%s output_language=%s file=%s cursor_line=%s",
        client.provider,
        _display_model(client.model),
        _safe_host(client.host),
        stream,
        len(prompt),
        language,
        output_language.code,
        request.file_name or "unknown",
        request.cursor_line or "unknown",
    )


def _log_llm_success(client: LlmClient, stream: bool, started_at: float, output_chars: int) -> None:
    LOGGER.info(
        "llm call success provider=%s model=%s stream=%s output_chars=%d elapsed_ms=%d",
        client.provider,
        _display_model(client.model),
        stream,
        output_chars,
        _elapsed_ms(started_at),
    )


def _log_llm_failure(client: LlmClient, exc: OSError, stream: bool, started_at: float) -> None:
    LOGGER.warning(
        "llm call failed provider=%s model=%s stream=%s elapsed_ms=%d error_type=%s error=%s",
        client.provider,
        _display_model(client.model),
        stream,
        _elapsed_ms(started_at),
        type(exc).__name__,
        exc,
    )


def _log_llm_fallback(client: LlmClient, reason: str, stream: bool, started_at: float) -> None:
    LOGGER.warning(
        "llm fallback provider=%s model=%s stream=%s elapsed_ms=%d reason=%s",
        client.provider,
        _display_model(client.model),
        stream,
        _elapsed_ms(started_at),
        reason,
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _display_model(model: str | None) -> str:
    return model or "<unspecified>"


def _safe_host(host: str) -> str:
    # Hosts can contain path information, but should never include API keys.
    return re.sub(r"([?&](?:api[_-]?key|key|token)=)[^&]+", r"\1<redacted>", host, flags=re.IGNORECASE)


@lru_cache(maxsize=32)
def _resolve_hostname(hostname: str) -> str | None:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for family, _, _, _, sockaddr in infos:
        if family in {socket.AF_INET, socket.AF_INET6}:
            return str(sockaddr[0])
    return None


def _merge_headers(headers: dict[str, str] | None, extra_headers: tuple[tuple[str, str], ...]) -> dict[str, str]:
    merged = dict(headers or {})
    for key, value in extra_headers:
        merged.setdefault(key, value)
    return merged


def _is_ip_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _format_host_for_netloc(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def _parallel_sections_enabled() -> bool:
    env_value = os.environ.get("LEGACYLENS_LLM_PARALLEL_SECTIONS")
    if env_value is not None:
        return _bool_value(env_value, default=False)
    try:
        return load_llm_config().parallel_sections
    except ValueError:
        return False


def _parallel_section_limit() -> int:
    env_value = os.environ.get("LEGACYLENS_LLM_PARALLEL_SECTION_LIMIT")
    if env_value is not None:
        try:
            return max(1, int(float(env_value)))
        except ValueError:
            return 4
    try:
        return max(1, load_llm_config().parallel_section_limit)
    except ValueError:
        return 4


def _build_section_prompts(
    request: AnalysisRequest,
    language: str,
    findings: list[Finding],
    context: ProjectContext | None,
    output_language: OutputLanguage,
) -> list[SectionPrompt]:
    shared_prompt = _build_prompt_shared_context(request, language, findings, context, output_language)
    section_instructions = (
        ("What this hovered code does at runtime, with concrete variables and control flow.", output_language.section_names[0]),
        ("What role this file or snippet appears to play in the current directory or project, using only supplied evidence.", output_language.section_names[1]),
        ("What callers, data, or files may be affected, and what evidence supports that impact.", output_language.section_names[2]),
        ("What to inspect next, including missing evidence or constraints that should be checked.", output_language.section_names[3]),
    )
    prompts: list[SectionPrompt] = []
    for index, (instruction, heading) in enumerate(section_instructions):
        prompts.append(
            SectionPrompt(
                index=index,
                heading=heading,
                prompt=(
                    f"{shared_prompt}\n\n"
                    "Write exactly one Markdown section.\n"
                    f"Use the heading `## {heading}`.\n"
                    f"Focus only on: {instruction}\n"
                    "Include 2-4 concise bullets grounded in the provided evidence.\n"
                    "Do not include any other section headings, introduction, conclusion, or fenced code block."
                ),
            )
        )
    return prompts


def _normalize_section_markdown(heading: str, markdown: str) -> str:
    cleaned = markdown.strip()
    if not cleaned:
        return ""
    if cleaned.startswith(f"## {heading}"):
        return cleaned
    if cleaned.startswith(f"### {heading}"):
        return f"## {heading}\n" + cleaned.split("\n", 1)[1].lstrip() if "\n" in cleaned else f"## {heading}"
    return f"## {heading}\n{cleaned}"


def _build_prompt(
    request: AnalysisRequest,
    language: str,
    findings: list[Finding],
    context: ProjectContext | None,
    output_language: OutputLanguage,
) -> str:
    shared_prompt = _build_prompt_shared_context(request, language, findings, context, output_language)
    section_names = ", ".join(output_language.section_names)
    return (
        f"{shared_prompt}\n\n"
        "Your answer must focus on these points in this order:\n"
        "1. What this hovered code does at runtime, using concrete variable names and control flow.\n"
        "2. What role this file or snippet appears to play in the current directory or project, based "
        "only on the supplied file list and symbol references.\n"
        "3. What role can be supported by evidence. Do not infer a relationship "
        "from file co-location alone. If Related files and Symbol references are empty, say the snippet "
        "looks standalone in the supplied context.\n"
        "4. What callers/data/files may be affected, if there is evidence. If evidence is missing, "
        "say that explicitly instead of inventing.\n"
        "5. What to inspect next. Mention historical constraints only when they directly explain a "
        "specific construct.\n\n"
        f"Return Markdown with these section headings: {section_names}."
    )


def _build_prompt_shared_context(
    request: AnalysisRequest,
    language: str,
    findings: list[Finding],
    context: ProjectContext | None,
    output_language: OutputLanguage,
) -> str:
    findings_text = "\n".join(
        f"- {finding.title} at line {finding.span.start_line}: {finding.rationale}"
        for finding in findings[:6]
    )
    context_text = _format_context_for_prompt(context)
    numbered_code_excerpt = _numbered_code_excerpt(request, limit=120)
    allowed_lines = ", ".join(str(line) for line in _allowed_line_numbers(request, findings, context))
    return (
        "You are Legacy Lens, a code-reading assistant for legacy projects. The user hovers on a "
        "small code region and wants practical understanding, not a generic history lesson.\n\n"
        f"Write concise Markdown in {output_language.prompt_name}. If you cannot reliably write accurate technical "
        f"analysis in {output_language.prompt_name}, write the whole answer in English instead. Do not mix languages "
        "except for code identifiers, file paths, API names, and short quoted code. Translate analyzer findings and "
        "idiom notes when summarizing them. Avoid repeating stock phrases. Do not include "
        "hidden reasoning, chain-of-thought, or <think> blocks.\n\n"
        "Line number rules are strict:\n"
        "- The numbered code excerpt below uses REAL file line numbers, not relative snippet lines.\n"
        "- Only cite a line number if it appears in Allowed evidence line numbers and the visible line text directly supports the claim.\n"
        f"- Never invent a line number. If no exact line supports a claim, say '{output_language.near_code_phrase}' instead of giving a line number.\n"
        "- Prefer quoting the exact identifier or expression over adding extra line numbers.\n\n"
        f"Language: {language}\n"
        f"Output language: {output_language.prompt_name} ({output_language.code}); fallback language: English\n"
        f"File: {request.file_name or 'unknown'}\n"
        f"Hovered file line: {request.cursor_line or 'unknown'}\n"
        f"Excerpt starts at file line: {request.excerpt_start_line}\n"
        f"Allowed evidence line numbers: {allowed_lines or 'none'}\n"
        f"Findings:\n{findings_text or '- none'}\n\n"
        f"Directory/project context:\n{context_text or '- none'}\n\n"
        f"Numbered code excerpt:\n```text\n{numbered_code_excerpt}\n```\n\n"
        "Ground every claim in the supplied evidence. If evidence is missing, say that explicitly."
    )


def _format_context_for_prompt(context: ProjectContext | None) -> str:
    if context is None:
        return ""
    lines = [
        f"- Scope: {context.scope}",
        f"- Root: {context.root or 'unknown'}",
        f"- Current directory: {context.current_directory or 'unknown'}",
        f"- Current file: {context.current_file or 'unknown'}",
    ]
    if context.files:
        lines.append("- Files:")
        lines.extend(f"  - {path}" for path in context.files[:80])
    if context.related_files:
        lines.append("- Related files:")
        lines.extend(f"  - {path}" for path in context.related_files[:20])
    else:
        lines.append("- Related files: none detected")
    if context.symbol_references:
        lines.append("- Symbol references:")
        for reference in context.symbol_references[:16]:
            lines.append(
                f"  - {reference.get('symbol')} in {reference.get('path')}:{reference.get('line')}: "
                f"{reference.get('text')}"
            )
    else:
        lines.append("- Symbol references: none detected")
    if context.notes:
        lines.append("- Notes:")
        lines.extend(f"  - {note}" for note in context.notes)
    return "\n".join(lines)


def _numbered_code_excerpt(request: AnalysisRequest, limit: int = 120) -> str:
    lines = request.code.splitlines()
    rendered: list[str] = []
    for index, text in enumerate(lines[:limit], start=request.excerpt_start_line):
        marker = " <-- hover" if request.cursor_line == index else ""
        rendered.append(f"{index:>6} | {text}{marker}")
    if len(lines) > limit:
        rendered.append(f"... excerpt truncated after {limit} lines ...")
    return "\n".join(rendered)


def _allowed_line_numbers(
    request: AnalysisRequest,
    findings: list[Finding],
    context: ProjectContext | None,
) -> list[int]:
    allowed: set[int] = set()
    if request.cursor_line is not None:
        allowed.add(request.cursor_line)
    for finding in findings:
        allowed.update(range(finding.span.start_line, finding.span.end_line + 1))
    if context:
        for reference in context.symbol_references:
            line = reference.get("line")
            if isinstance(line, int):
                allowed.add(line)
    return sorted(allowed)


def _line_reference_warning(
    markdown: str,
    request: AnalysisRequest,
    findings: list[Finding],
    context: ProjectContext | None,
    output_language: OutputLanguage | None = None,
) -> str | None:
    referenced = _extract_line_references(markdown)
    if not referenced:
        return None
    allowed = set(_allowed_line_numbers(request, findings, context))
    invalid = sorted(line for line in referenced if line not in allowed)
    if not invalid:
        return None
    invalid_text = ", ".join(str(line) for line in invalid)
    if _warning_language(output_language) == "zh-Hans":
        return (
            "模型提到了未被悬停行、静态命中或符号引用支持的行号 "
            f"{invalid_text}；这些行号应忽略，以编号代码片段和命中结果为准。"
        )
    if _warning_language(output_language) == "zh-Hant":
        return (
            "模型提到了未被懸停行、靜態命中或符號引用支持的行號 "
            f"{invalid_text}；這些行號應忽略，以編號程式碼片段和命中結果為準。"
        )
    return (
        "The model mentioned line numbers that are not supported by the hovered line, static findings, "
        f"or symbol references: {invalid_text}. Ignore those line references and use the numbered excerpt "
        "and findings as the evidence."
    )


def _append_line_reference_warning(
    markdown: str,
    request: AnalysisRequest,
    findings: list[Finding],
    context: ProjectContext | None,
    output_language: OutputLanguage | None = None,
) -> str:
    warning = _line_reference_warning(markdown, request, findings, context, output_language)
    if not warning:
        return markdown
    return f"{markdown}\n\n> {_line_warning_label(output_language)}: {warning}"


def _extract_line_references(markdown: str) -> set[int]:
    references: set[int] = set()
    patterns = (
        r"第\s*(\d{1,6})\s*行",
        r"(\d{1,6})\s*行",
        r"\bline\s+(\d{1,6})\b",
        r"\bligne\s+(\d{1,6})\b",
        r"\bl[ií]nea\s+(\d{1,6})\b",
        r"\bzeile\s+(\d{1,6})\b",
        r"\briga\s+(\d{1,6})\b",
        r"\blinha\s+(\d{1,6})\b",
        r"\bL(\d{1,6})\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, markdown, flags=re.IGNORECASE):
            try:
                references.add(int(match.group(1)))
            except ValueError:
                continue
    return references


def _line_warning_label(output_language: OutputLanguage | None = None) -> str:
    warning_language = _warning_language(output_language)
    if warning_language == "zh-Hans":
        return "行号校验"
    if warning_language == "zh-Hant":
        return "行號校驗"
    return "Line check"


def _is_simplified_chinese(output_language: OutputLanguage | None = None) -> bool:
    return (output_language or ENGLISH).deterministic_language == "zh-Hans"


def _warning_language(output_language: OutputLanguage | None = None) -> str:
    return (output_language or ENGLISH).code


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_raw_string(*values: Any) -> str:
    for value in values:
        if value is not None:
            return str(value)
    return ""


def _normalize_mode(value: str | None) -> str:
    normalized = (value or "local").strip().lower().replace("_", "-")
    if normalized in {"api", "remote", "http", "https", "openai", "openai-compatible", "chat-completions"}:
        return "api"
    return "local"


def _preference_list(*values: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            items = tuple(item.strip() for item in value.split(",") if item.strip())
            if items:
                return items
        if isinstance(value, list):
            items = tuple(str(item).strip() for item in value if str(item).strip())
            if items:
                return items
    return default


def _bool_value(*values: Any, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)
    return default


def _float_value(*values: Any, default: float) -> float:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _float_from_environment(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _render_deterministic(
    language: str,
    findings: list[Finding],
    context: ProjectContext | None = None,
    request: AnalysisRequest | None = None,
    output_language: OutputLanguage | None = None,
) -> str:
    if _is_simplified_chinese(output_language):
        return _render_deterministic_zh(language, findings, context, request)
    return _render_deterministic_en(language, findings, context, request)


def _render_deterministic_en(
    language: str,
    findings: list[Finding],
    context: ProjectContext | None = None,
    request: AnalysisRequest | None = None,
) -> str:
    focus_line = _focus_line(request) if request else ""
    if not findings:
        lines = ["### Legacy Lens", "", "**Behavior**"]
        if focus_line:
            lines.append(f"- No high-confidence static rule matched near the hovered line; current line: `{focus_line}`.")
        else:
            lines.append(f"- No high-confidence static rule matched this {language} snippet.")
        lines.extend(_context_summary_lines_en(context))
        lines.extend(["", "**Next Checks**", "- Expand the hover context, or enable directory/project context and analyze again."])
        return "\n".join(lines)

    primary = findings[0]
    lines = [
        "### Legacy Lens",
        "",
        "**Behavior**",
        f"- Line {primary.span.start_line} `{primary.span.text.strip()}` triggered `{primary.rule_id}` ({primary.title}).",
    ]
    if focus_line and focus_line != primary.span.text.strip():
        lines.append(f"- Hovered line: `{focus_line}`.")
    for finding in findings[1:4]:
        lines.append(f"- Line {finding.span.start_line} `{finding.span.text.strip()}` triggered `{finding.rule_id}` ({finding.title}).")

    lines.extend(_context_summary_lines_en(context))

    hints = [hint for hint in dict.fromkeys(finding.remediation_hint for finding in findings[:5] if finding.remediation_hint) if _mostly_ascii(hint)]
    if hints:
        lines.extend(["", "**Next Checks**"])
        for hint in hints:
            lines.append(f"- {hint}")
    elif findings:
        lines.extend(["", "**Next Checks**", "- Inspect callers, inputs, side effects, and nearby error handling before changing this code."])
    return "\n".join(lines)


def _render_deterministic_zh(
    language: str,
    findings: list[Finding],
    context: ProjectContext | None = None,
    request: AnalysisRequest | None = None,
) -> str:
    focus_line = _focus_line(request) if request else ""
    if not findings:
        lines = ["### Legacy Lens", "", "**行为**"]
        if focus_line:
            lines.append(f"- 悬停行附近没有命中高置信度规则；当前行是：`{focus_line}`。")
        else:
            lines.append(f"- 未在这段 {language} 代码中命中高置信度规则。")
        lines.extend(_context_summary_lines_zh(context))
        lines.extend(["", "**下一步检查**", "- 扩大悬停上下文，或打开目录/项目上下文后再次分析。"])
        return "\n".join(lines)

    primary = findings[0]
    lines = [
        "### Legacy Lens",
        "",
        "**行为**",
        f"- 第 {primary.span.start_line} 行 `{primary.span.text.strip()}`：{primary.rationale}",
    ]
    if focus_line and focus_line != primary.span.text.strip():
        lines.append(f"- 当前悬停行：`{focus_line}`。")
    for finding in findings[1:4]:
        lines.append(f"- 第 {finding.span.start_line} 行 `{finding.span.text.strip()}`：{finding.rationale}")

    lines.extend(_context_summary_lines_zh(context))

    hints = list(dict.fromkeys(finding.remediation_hint for finding in findings[:5] if finding.remediation_hint))
    if hints:
        lines.extend(["", "**下一步检查**"])
        for hint in hints:
            lines.append(f"- {hint}")
    return "\n".join(lines)


def _context_summary_lines_en(context: ProjectContext | None) -> list[str]:
    lines = ["", "**Role In Current Context**"]
    if context is None:
        lines.append("- No directory or project context was provided, so only the snippet itself can be explained.")
        return lines
    if context.related_files:
        examples = ", ".join(context.related_files[:5])
        lines.append(f"- Found {len(context.related_files)} related files in `{context.scope}` scope, for example: {examples}.")
    elif context.files:
        lines.append(f"- The supplied context has {len(context.files)} files, but no clearly related files were detected.")
    else:
        lines.append("- No readable directory/project files were found.")
    if context.symbol_references:
        lines.extend(["", "**Impact**"])
        for reference in context.symbol_references[:5]:
            lines.append(
                f"- `{reference.get('symbol')}` appears in `{reference.get('path')}:"
                f"{reference.get('line')}`: {reference.get('text')}"
            )
    else:
        lines.extend(["", "**Impact**", "- No cross-file symbol references were detected, so external callers cannot be inferred from this context."])
    return lines


def _context_summary_lines_zh(context: ProjectContext | None) -> list[str]:
    lines = ["", "**在当前目录/项目中的作用**"]
    if context is None:
        lines.append("- 未提供目录或项目上下文，因此只能解释片段本身。")
        return lines
    if context.related_files:
        examples = ", ".join(context.related_files[:5])
        lines.append(f"- 在 `{context.scope}` 范围内找到了 {len(context.related_files)} 个相关文件，例如：{examples}。")
    elif context.files:
        lines.append(f"- 上下文中有 {len(context.files)} 个文件，但没有发现明显相关文件。")
    else:
        lines.append("- 没有扫描到可用的同目录/项目文件。")
    if context.symbol_references:
        lines.extend(["", "**影响面**"])
        for reference in context.symbol_references[:5]:
            lines.append(
                f"- `{reference.get('symbol')}` 出现在 `{reference.get('path')}:"
                f"{reference.get('line')}`：{reference.get('text')}"
            )
    else:
        lines.extend(["", "**影响面**", "- 未发现跨文件符号引用；不能据此判断外部调用方。"])
    return lines


def _mostly_ascii(text: str) -> bool:
    if not text:
        return False
    ascii_count = sum(1 for char in text if ord(char) < 128)
    return ascii_count / len(text) > 0.85


def _focus_line(request: AnalysisRequest) -> str:
    relative_cursor_line = request.relative_cursor_line()
    if not relative_cursor_line:
        return ""
    lines = request.code.splitlines()
    if 1 <= relative_cursor_line <= len(lines):
        return lines[relative_cursor_line - 1].strip()
    return ""
