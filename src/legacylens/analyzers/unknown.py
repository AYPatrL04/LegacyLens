from __future__ import annotations

from legacylens.models import Finding

from .assembly import AssemblyAnalyzer
from .base import Analyzer
from .c_like import CLikeAnalyzer
from .cobol import CobolAnalyzer
from .fortran import FortranAnalyzer
from .mainstream import MainstreamAnalyzer


class UnknownAnalyzer(Analyzer):
    language = "unknown"

    def analyze(self, code: str) -> list[Finding]:
        findings: list[Finding] = []
        for analyzer in (
            CLikeAnalyzer(),
            FortranAnalyzer(),
            CobolAnalyzer(),
            AssemblyAnalyzer(),
            MainstreamAnalyzer("unknown", include_common=True),
        ):
            findings.extend(analyzer.analyze(code))
        return findings
