from __future__ import annotations

from dataclasses import replace

from .analyzers import (
    AssemblyAnalyzer,
    CLikeAnalyzer,
    CobolAnalyzer,
    FortranAnalyzer,
    UnknownAnalyzer,
    mainstream_analyzer,
)
from .context import build_project_context
from .facts import FactStore
from .i18n import resolve_output_language
from .language import detect_language
from .llm import Explainer
from .models import AnalysisRequest, AnalysisResponse, Finding, Severity


class LegacyLensEngine:
    def __init__(self, fact_store: FactStore | None = None, explainer: Explainer | None = None) -> None:
        self.fact_store = fact_store or FactStore()
        self.explainer = explainer or Explainer()

    def inspect(self, request: AnalysisRequest) -> AnalysisResponse:
        language = detect_language(request.code, file_name=request.file_name, explicit=request.language)
        analyzer = self._analyzer_for(language)
        findings = analyzer.analyze(request.code)
        findings = _rank_findings(findings, cursor_line=request.relative_cursor_line())[: request.max_findings]
        findings = _shift_findings_to_file_lines(findings, request.excerpt_start_line)
        facts = self.fact_store.retrieve(findings, query=request.code, limit=3)
        context = build_project_context(request, language)
        return AnalysisResponse(
            language=language,
            output_language=resolve_output_language(request.output_language, request.ui_language).code,
            findings=findings,
            facts=facts,
            context=context,
        )

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        inspected = self.inspect(request)
        explanation = self.explainer.explain(
            request,
            language=inspected.language,
            findings=inspected.findings,
            facts=inspected.facts,
            context=inspected.context,
        )
        return AnalysisResponse(
            language=inspected.language,
            output_language=inspected.output_language,
            findings=inspected.findings,
            facts=inspected.facts,
            context=inspected.context,
            markdown=explanation.markdown,
            model_used=explanation.model_used,
            fallback_reason=explanation.fallback_reason,
        )

    def _analyzer_for(self, language: str):
        if language in {"c", "cpp"}:
            return CLikeAnalyzer(language=language)
        if language == "fortran":
            return FortranAnalyzer()
        if language == "cobol":
            return CobolAnalyzer()
        if language == "asm":
            return AssemblyAnalyzer()
        analyzer = mainstream_analyzer(language)
        if analyzer is not None:
            return analyzer
        return UnknownAnalyzer()


def _rank_findings(findings: list[Finding], cursor_line: int | None = None) -> list[Finding]:
    severity_rank = {
        Severity.HIGH: 0,
        Severity.MEDIUM: 1,
        Severity.LOW: 2,
        Severity.INFO: 3,
    }

    def key(finding: Finding) -> tuple[int, int, int]:
        distance = abs(finding.span.start_line - cursor_line) if cursor_line else 0
        return (distance, severity_rank[finding.severity], finding.span.start_line)

    return sorted(findings, key=key)


def _shift_findings_to_file_lines(findings: list[Finding], excerpt_start_line: int) -> list[Finding]:
    offset = max(0, excerpt_start_line - 1)
    if offset == 0:
        return findings
    shifted: list[Finding] = []
    for finding in findings:
        shifted.append(
            replace(
                finding,
                span=replace(
                    finding.span,
                    start_line=finding.span.start_line + offset,
                    end_line=finding.span.end_line + offset,
                ),
            )
        )
    return shifted
