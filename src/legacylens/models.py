from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class SourceSpan:
    start_line: int
    end_line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    language: str
    title: str
    severity: Severity
    span: SourceSpan
    rationale: str
    historical_context: str
    remediation_hint: str | None = None
    tags: tuple[str, ...] = ()
    confidence: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["tags"] = list(self.tags)
        return payload


@dataclass(frozen=True)
class AnalysisRequest:
    code: str
    language: str | None = None
    output_language: str | None = None
    ui_language: str | None = None
    file_name: str | None = None
    project_root: str | None = None
    excerpt_start_line: int = 1
    cursor_line: int | None = None
    max_findings: int = 8
    use_llm: bool = False
    context_scope: str = "none"

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AnalysisRequest":
        return cls(
            code=str(payload.get("code", "")),
            language=payload.get("language"),
            output_language=_first_present(payload, "output_language", "outputLanguage", "locale"),
            ui_language=_first_present(payload, "ui_language", "uiLanguage", "editor_language", "editorLanguage", "display_language", "displayLanguage"),
            file_name=_first_present(payload, "file_name", "fileName"),
            project_root=_first_present(payload, "project_root", "projectRoot"),
            excerpt_start_line=max(1, _optional_int(_first_present(payload, "excerpt_start_line", "excerptStartLine")) or 1),
            cursor_line=_optional_int(_first_present(payload, "cursor_line", "cursorLine")),
            max_findings=max(1, _optional_int(_first_present(payload, "max_findings", "maxFindings")) or 8),
            use_llm=_optional_bool(_first_present(payload, "use_llm", "useLlm")),
            context_scope=_normalize_context_scope(_first_present(payload, "context_scope", "contextScope")),
        )

    def relative_cursor_line(self) -> int | None:
        if self.cursor_line is None:
            return None
        relative = self.cursor_line - self.excerpt_start_line + 1
        if 1 <= relative <= len(self.code.splitlines()):
            return relative
        if 1 <= self.cursor_line <= len(self.code.splitlines()):
            return self.cursor_line
        return None

    def excerpt_line_numbers(self) -> list[int]:
        return list(range(self.excerpt_start_line, self.excerpt_start_line + len(self.code.splitlines())))


@dataclass(frozen=True)
class ProjectContext:
    scope: str
    root: str | None = None
    current_directory: str | None = None
    current_file: str | None = None
    files: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    symbol_references: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisResponse:
    language: str
    findings: list[Finding] = field(default_factory=list)
    context: ProjectContext | None = None
    markdown: str = ""
    model_used: str | None = None
    fallback_reason: str | None = None
    output_language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "findings": [finding.to_dict() for finding in self.findings],
            "context": self.context.to_dict() if self.context else None,
            "markdown": self.markdown,
            "model_used": self.model_used,
            "fallback_reason": self.fallback_reason,
            "output_language": self.output_language,
        }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _normalize_context_scope(value: Any) -> str:
    if value is None:
        return "none"
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized in {"workspace", "project", "repo", "repository"}:
        return "project"
    if normalized in {"dir", "directory", "current-directory", "current-dir", "folder"}:
        return "directory"
    if normalized in {"none", "off", "false", "0"}:
        return "none"
    return "none"
