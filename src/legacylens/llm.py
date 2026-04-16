from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

from .models import AnalysisRequest, Fact, Finding, ProjectContext

DEFAULT_MODEL_PREFERENCES = (
    "qwen",
    "deepseek",
    "codellama",
    "codegemma",
    "llama",
    "mistral",
)
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Explanation:
    markdown: str
    model_used: str | None = None
    fallback_reason: str | None = None


class Explainer:
    def __init__(self, client: "OllamaClient | None" = None) -> None:
        self.client = client
        self._client_checked = client is not None

    def explain(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        facts: list[Fact],
        context: ProjectContext | None = None,
    ) -> Explanation:
        prompt = _build_prompt(request, language, findings, facts, context)
        if not request.use_llm:
            return Explanation(markdown=_render_deterministic(language, findings, facts, context, request))

        self._ensure_client()

        if self.client is not None:
            try:
                markdown = self.client.generate(prompt)
                if markdown:
                    return Explanation(
                        markdown=_append_line_reference_warning(markdown, request, findings, context),
                        model_used=self.client.model,
                    )
                return Explanation(
                    markdown=_render_deterministic(language, findings, facts, context, request),
                    fallback_reason=f"Ollama model {self.client.model} returned an empty response.",
                )
            except OSError as exc:
                return Explanation(
                    markdown=_render_deterministic(language, findings, facts, context, request),
                    fallback_reason=f"Ollama unavailable: {exc}",
                )

        return Explanation(
            markdown=_render_deterministic(language, findings, facts, context, request),
            fallback_reason="Ollama model was not configured or auto-discovered.",
        )

    def explain_stream(
        self,
        request: AnalysisRequest,
        language: str,
        findings: list[Finding],
        facts: list[Fact],
        context: ProjectContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        prompt = _build_prompt(request, language, findings, facts, context)
        if not request.use_llm:
            markdown = _render_deterministic(language, findings, facts, context, request)
            yield {
                "type": "delta",
                "text": markdown,
            }
            yield {"type": "done", "model_used": None, "fallback_reason": None}
            return

        self._ensure_client()
        if self.client is None:
            reason = "Ollama model was not configured or auto-discovered."
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, facts, context, request),
            }
            yield {"type": "done", "model_used": None, "fallback_reason": reason}
            return

        emitted = False
        chunks: list[str] = []
        try:
            for text in self.client.generate_stream(prompt):
                emitted = True
                chunks.append(text)
                yield {"type": "delta", "text": text}
        except OSError as exc:
            reason = f"Ollama unavailable: {exc}"
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, facts, context, request),
            }
            yield {"type": "done", "model_used": self.client.model, "fallback_reason": reason}
            return

        if not emitted:
            reason = f"Ollama model {self.client.model} returned an empty response."
            yield {"type": "fallback", "reason": reason}
            yield {
                "type": "delta",
                "text": _render_deterministic(language, findings, facts, context, request),
            }
            yield {"type": "done", "model_used": self.client.model, "fallback_reason": reason}
            return

        warning = _line_reference_warning("".join(chunks), request, findings, context)
        if warning:
            yield {"type": "delta", "text": f"\n\n> 行号校验：{warning}"}
        yield {"type": "done", "model_used": self.client.model, "fallback_reason": None}

    def model_status(self) -> dict[str, str | bool | None]:
        self._ensure_client()
        return {
            "available": self.client is not None,
            "model": self.client.model if self.client else None,
            "host": self.client.host if self.client else normalize_ollama_host(os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)),
        }

    def _ensure_client(self) -> None:
        if self.client is None and not self._client_checked:
            self.client = OllamaClient.from_environment()
            self._client_checked = True


@dataclass(frozen=True)
class OllamaClient:
    host: str
    model: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_environment(cls) -> "OllamaClient | None":
        host = normalize_ollama_host(os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
        timeout = _float_from_environment("LEGACYLENS_OLLAMA_TIMEOUT", default=60.0)
        model = os.environ.get("LEGACYLENS_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL")
        if not model and not _truthy(os.environ.get("LEGACYLENS_DISABLE_OLLAMA_AUTODISCOVERY")):
            try:
                model = discover_ollama_model(host)
            except OSError:
                model = None
        if not model:
            return None
        return cls(host=host, model=model, timeout_seconds=timeout)

    @classmethod
    def discover(cls, host: str | None = None) -> "OllamaClient | None":
        resolved_host = normalize_ollama_host(host or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
        model = discover_ollama_model(resolved_host)
        if not model:
            return None
        return cls(host=resolved_host, model=model)

    def generate(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 700},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise OSError(str(exc)) from exc
        raw = str(data.get("response", "")).strip()
        cleaned = _strip_thinking(raw)
        return cleaned or raw

    def generate_stream(self, prompt: str) -> Iterator[str]:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 700},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = str(data.get("response", ""))
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except urllib.error.URLError as exc:
            raise OSError(str(exc)) from exc


def discover_ollama_model(host: str = DEFAULT_OLLAMA_HOST) -> str | None:
    models = list_ollama_models(host=host)
    return select_preferred_model(models)


def list_ollama_models(host: str = DEFAULT_OLLAMA_HOST, timeout_seconds: float = 2.0) -> list[str]:
    resolved_host = normalize_ollama_host(host)
    request = urllib.request.Request(f"{resolved_host}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise OSError(str(exc)) from exc

    models = data.get("models", [])
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get("name"), str):
            names.append(model["name"])
    return names


def select_preferred_model(models: list[str]) -> str | None:
    if not models:
        return None
    preferred = [
        item.strip().lower()
        for item in os.environ.get("LEGACYLENS_OLLAMA_PREFER", ",".join(DEFAULT_MODEL_PREFERENCES)).split(",")
        if item.strip()
    ]
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


def _build_prompt(
    request: AnalysisRequest,
    language: str,
    findings: list[Finding],
    facts: list[Fact],
    context: ProjectContext | None,
) -> str:
    findings_text = "\n".join(
        f"- {finding.title} at line {finding.span.start_line}: {finding.rationale}"
        for finding in findings[:6]
    )
    facts_text = "\n".join(f"- {fact.title}: {fact.summary}" for fact in facts[:3])
    context_text = _format_context_for_prompt(context)
    numbered_code_excerpt = _numbered_code_excerpt(request, limit=120)
    allowed_lines = ", ".join(str(line) for line in _allowed_line_numbers(request, findings, context))
    return (
        "You are Legacy Lens, a code-reading assistant for legacy projects. The user hovers on a "
        "small code region and wants practical understanding, not a generic history lesson.\n\n"
        "Write concise Simplified Chinese Markdown. Avoid repeating stock phrases. Do not include "
        "hidden reasoning, chain-of-thought, or <think> blocks.\n\n"
        "Line number rules are strict:\n"
        "- The numbered code excerpt below uses REAL file line numbers, not relative snippet lines.\n"
        "- Only cite a line number if it appears in Allowed evidence line numbers and the visible line text directly supports the claim.\n"
        "- Never invent a line number. If no exact line supports a claim, say '鍦ㄨ繖娈典唬鐮侀檮杩? instead of giving a line number.\n"
        "- Prefer quoting the exact identifier or expression over adding extra line numbers.\n\n"
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
        f"Language: {language}\n"
        f"File: {request.file_name or 'unknown'}\n"
        f"Hovered file line: {request.cursor_line or 'unknown'}\n"
        f"Excerpt starts at file line: {request.excerpt_start_line}\n"
        f"Allowed evidence line numbers: {allowed_lines or 'none'}\n"
        f"Findings:\n{findings_text or '- none'}\n\n"
        f"Directory/project context:\n{context_text or '- none'}\n\n"
        f"Idiom notes, use only when relevant:\n{facts_text or '- none'}\n\n"
        f"Numbered code excerpt:\n```text\n{numbered_code_excerpt}\n```\n\n"
        "Return Markdown with sections: 行为, 在当前目录/项目中的作用, 影响面, 下一步检查."
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
) -> str | None:
    referenced = _extract_line_references(markdown)
    if not referenced:
        return None
    allowed = set(_allowed_line_numbers(request, findings, context))
    invalid = sorted(line for line in referenced if line not in allowed)
    if not invalid:
        return None
    return (
        "模型提到了未被悬停行、静态命中或符号引用支持的行号 "
        f"{', '.join(str(line) for line in invalid)}；这些行号应忽略，以编号代码片段和命中结果为准。"
    )


def _append_line_reference_warning(
    markdown: str,
    request: AnalysisRequest,
    findings: list[Finding],
    context: ProjectContext | None,
) -> str:
    warning = _line_reference_warning(markdown, request, findings, context)
    if not warning:
        return markdown
    return f"{markdown}\n\n> 行号校验：{warning}"


def _extract_line_references(markdown: str) -> set[int]:
    references: set[int] = set()
    patterns = (
        r"第\s*(\d{1,6})\s*行",
        r"\bline\s+(\d{1,6})\b",
        r"\bL(\d{1,6})\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, markdown, flags=re.IGNORECASE):
            try:
                references.add(int(match.group(1)))
            except ValueError:
                continue
    return references


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
    facts: list[Fact],
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
        lines.extend(_context_summary_lines(context))
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

    lines.extend(_context_summary_lines(context))

    if facts:
        lines.extend(["", "**相关惯用法**"])
        for fact in facts[:2]:
            lines.append(f"- {fact.title}: {fact.summary}")
    hints = list(dict.fromkeys(finding.remediation_hint for finding in findings[:5] if finding.remediation_hint))
    if hints:
        lines.extend(["", "**下一步检查**"])
        for hint in hints:
            lines.append(f"- {hint}")
    return "\n".join(lines)


def _context_summary_lines(context: ProjectContext | None) -> list[str]:
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


def _focus_line(request: AnalysisRequest) -> str:
    relative_cursor_line = request.relative_cursor_line()
    if not relative_cursor_line:
        return ""
    lines = request.code.splitlines()
    if 1 <= relative_cursor_line <= len(lines):
        return lines[relative_cursor_line - 1].strip()
    return ""
