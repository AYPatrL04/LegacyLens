from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Pattern

from legacylens.models import Finding, Severity, SourceSpan


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    pattern: Pattern[str]
    severity: Severity
    rationale: str
    historical_context: str
    remediation_hint: str | None
    tags: tuple[str, ...]
    confidence: float = 0.75


class Analyzer:
    language = "unknown"

    def analyze(self, code: str) -> list[Finding]:
        raise NotImplementedError

    def _scan_rules(self, code: str, rules: Iterable[Rule]) -> list[Finding]:
        findings: list[Finding] = []
        lines = code.splitlines()
        for index, line in enumerate(lines, start=1):
            for rule in rules:
                if rule.pattern.search(line):
                    findings.append(
                        Finding(
                            rule_id=rule.rule_id,
                            language=self.language,
                            title=rule.title,
                            severity=rule.severity,
                            span=SourceSpan(start_line=index, end_line=index, text=line.rstrip()),
                            rationale=rule.rationale,
                            historical_context=rule.historical_context,
                            remediation_hint=rule.remediation_hint,
                            tags=rule.tags,
                            confidence=rule.confidence,
                        )
                    )
        return findings


def regex(pattern: str, flags: int = re.IGNORECASE) -> Pattern[str]:
    return re.compile(pattern, flags)
