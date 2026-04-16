from __future__ import annotations

from legacylens.models import Severity

from .base import Analyzer, Rule, regex


class FortranAnalyzer(Analyzer):
    language = "fortran"

    RULES = (
        Rule(
            rule_id="fortran.common",
            title="COMMON block shared storage",
            pattern=regex(r"^\s*(?:\d+\s+)?COMMON\b"),
            severity=Severity.HIGH,
            rationale="COMMON 暴露可被多个程序单元共享的全局存储。",
            historical_context="Fortran 77 常用 COMMON 复用内存并传递跨单元状态。",
            remediation_hint="修改字段顺序或类型前，先映射所有 COMMON 布局和调用点。",
            tags=("common", "memory-overlay", "fortran"),
        ),
        Rule(
            rule_id="fortran.equivalence",
            title="EQUIVALENCE storage alias",
            pattern=regex(r"\bEQUIVALENCE\s*\("),
            severity=Severity.HIGH,
            rationale="EQUIVALENCE 让多个变量共享同一存储地址。",
            historical_context="这类别名常用于手工复用数组或标量存储。",
            remediation_hint="在测试证明前，把别名变量当成同一个二进制布局处理。",
            tags=("equivalence", "memory-overlay", "fortran"),
        ),
        Rule(
            rule_id="fortran.computed-goto",
            title="Computed GOTO dispatch",
            pattern=regex(r"\bGO\s*TO\s*\([^)]*\)|\bGOTO\s*\([^)]*\)"),
            severity=Severity.HIGH,
            rationale="跳转目标由整数表达式选择。",
            historical_context="Computed GOTO 常被当作紧凑跳转表使用。",
            remediation_hint="重构前先把每个目标标签恢复成命名状态。",
            tags=("goto", "jump-table", "fortran"),
        ),
        Rule(
            rule_id="fortran.goto",
            title="Unstructured GOTO",
            pattern=regex(r"\bGO\s*TO\b|\bGOTO\b"),
            severity=Severity.MEDIUM,
            rationale="这里把控制流转移到数字标签。",
            historical_context="早期 Fortran 常用标签跳转表达循环和错误出口。",
            remediation_hint="替换前先确认目标标签的职责。",
            tags=("goto", "control-flow", "fortran"),
        ),
        Rule(
            rule_id="fortran.arithmetic-if",
            title="Arithmetic IF tri-branch",
            pattern=regex(r"^\s*(?:\d+\s+)?IF\s*\(.+\)\s*\d+\s*,\s*\d+\s*,\s*\d+"),
            severity=Severity.HIGH,
            rationale="Arithmetic IF 按负、零、正三个结果跳到不同标签。",
            historical_context="这是以标签为主要控制流词汇时的紧凑三分支写法。",
            remediation_hint="先命名三个分支，再改写成结构化条件。",
            tags=("arithmetic-if", "goto", "fortran"),
        ),
        Rule(
            rule_id="fortran.labeled-do",
            title="Label-terminated DO loop",
            pattern=regex(r"^\s*(?:\d+\s+)?DO\s+\d+\s+[A-Za-z]\w*\s*="),
            severity=Severity.MEDIUM,
            rationale="循环结束位置由数字标签决定，而不是块分隔符。",
            historical_context="固定格式 Fortran 常复用标签，循环范围容易误读。",
            remediation_hint="编辑嵌套循环前，先标出匹配的终止标签。",
            tags=("do-loop", "label", "fortran"),
        ),
    )

    def analyze(self, code: str):
        return self._scan_rules(code, self.RULES)
