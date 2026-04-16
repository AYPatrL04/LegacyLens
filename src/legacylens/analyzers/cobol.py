from __future__ import annotations

from legacylens.models import Severity

from .base import Analyzer, Rule, regex


class CobolAnalyzer(Analyzer):
    language = "cobol"

    RULES = (
        Rule(
            rule_id="cobol.perform-thru",
            title="PERFORM THRU range call",
            pattern=regex(r"\bPERFORM\s+[A-Z0-9-]+\s+THRU\s+[A-Z0-9-]+"),
            severity=Severity.HIGH,
            rationale="PERFORM THRU 会按源码顺序执行一段 paragraph 范围。",
            historical_context="这种范围调用依赖源码顺序，移动 paragraph 可能改变行为。",
            remediation_hint="重排前列出范围内每个 paragraph。",
            tags=("perform-thru", "control-flow", "cobol"),
        ),
        Rule(
            rule_id="cobol.alter",
            title="ALTER modifies a jump target",
            pattern=regex(r"\bALTER\b.*\bTO\s+PROCEED\s+TO\b"),
            severity=Severity.HIGH,
            rationale="ALTER 会在运行时改变 paragraph 的跳转目标。",
            historical_context="ALTER 常用于状态机式流程，但会隐藏真实分支目标。",
            remediation_hint="把被 ALTER 的跳转建模为显式状态变量。",
            tags=("alter", "goto", "cobol"),
        ),
        Rule(
            rule_id="cobol.goto",
            title="GO TO transfer",
            pattern=regex(r"\bGO\s+TO\b"),
            severity=Severity.MEDIUM,
            rationale="这里跳转到另一个 paragraph 或 section。",
            historical_context="批处理 COBOL 常用 paragraph 跳转表达出口和分派。",
            remediation_hint="追踪目标 paragraph 以及后续 fall-through 区域。",
            tags=("goto", "control-flow", "cobol"),
        ),
        Rule(
            rule_id="cobol.redefines",
            title="REDEFINES data overlay",
            pattern=regex(r"\bREDEFINES\b"),
            severity=Severity.HIGH,
            rationale="REDEFINES 让多个记录布局共享同一段字节。",
            historical_context="固定宽度记录常用 REDEFINES 表达变体布局。",
            remediation_hint="先找出判别字段，再把记录当作具体 schema 处理。",
            tags=("redefines", "memory-overlay", "cobol"),
        ),
        Rule(
            rule_id="cobol.occurs-depending",
            title="Variable-length table",
            pattern=regex(r"\bOCCURS\b.*\bDEPENDING\s+ON\b"),
            severity=Severity.MEDIUM,
            rationale="表大小由另一个运行时字段决定。",
            historical_context="OCCURS DEPENDING ON 用于在固定记录中容纳可变数量项。",
            remediation_hint="读取或转换表前校验 count 字段。",
            tags=("variable-record", "packed-data", "cobol"),
        ),
        Rule(
            rule_id="cobol.next-sentence",
            title="NEXT SENTENCE control transfer",
            pattern=regex(r"\bNEXT\s+SENTENCE\b"),
            severity=Severity.LOW,
            rationale="NEXT SENTENCE 会跳到下一个句点后的语句。",
            historical_context="COBOL 中句点会影响控制流范围。",
            remediation_hint="修改附近 IF 或 PERFORM 前先检查句点位置。",
            tags=("punctuation-flow", "control-flow", "cobol"),
        ),
    )

    def analyze(self, code: str):
        return self._scan_rules(code, self.RULES)
