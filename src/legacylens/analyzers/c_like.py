from __future__ import annotations

from legacylens.models import Severity

from .base import Analyzer, Rule, regex


class CLikeAnalyzer(Analyzer):
    language = "c"

    RULES = (
        Rule(
            rule_id="c.goto",
            title="Explicit goto jump",
            pattern=regex(r"\bgoto\s+[A-Za-z_]\w*\s*;"),
            severity=Severity.MEDIUM,
            rationale="这里执行非结构化跳转。",
            historical_context="旧 C 代码常用 goto 表示状态切换、清理路径或循环出口。",
            remediation_hint="先命名目标状态，再判断是否能改成更清晰的分支或 helper。",
            tags=("goto", "control-flow", "c"),
        ),
        Rule(
            rule_id="c.union-overlay",
            title="Union memory overlay",
            pattern=regex(r"\bunion\b"),
            severity=Severity.MEDIUM,
            rationale="union 让多种视图共享同一段存储。",
            historical_context="内存紧张或需要解释设备数据时，overlay 是常见手段。",
            remediation_hint="记录每个视图的含义，并确认字节序和对齐假设。",
            tags=("memory-overlay", "union", "c"),
        ),
        Rule(
            rule_id="c.bit-packing",
            title="Bit-level packing or masking",
            pattern=regex(r"(?<![&])&(?![&])|(?<![|])\|(?![|])|\^|<<|>>"),
            severity=Severity.LOW,
            rationale="这里直接操作 bit，而不是使用命名字段。",
            historical_context="bit flag 常用于把多个布尔状态压进一个字节或字。",
            remediation_hint="补充命名 mask 或 enum，让每个 bit 的含义可追踪。",
            tags=("bit-packing", "flags", "c"),
        ),
        Rule(
            rule_id="c.macro-continuation",
            title="Multi-line preprocessor macro",
            pattern=regex(r"^\s*#\s*define\b.*\\\s*$"),
            severity=Severity.LOW,
            rationale="宏在类型检查前展开，可能隐藏控制流或副作用。",
            historical_context="宏常用于消除调用开销或模拟泛型。",
            remediation_hint="优先考虑 inline 函数；若保留宏，写清副作用。",
            tags=("macro", "preprocessor", "c"),
        ),
        Rule(
            rule_id="c.octal-literal",
            title="Octal-looking numeric literal",
            pattern=regex(r"(?<![\w.])0[0-7]{2,}\b"),
            severity=Severity.INFO,
            rationale="C 中前导 0 表示八进制，维护者容易误读。",
            historical_context="八进制常见于权限、字节和 bit mask 场景。",
            remediation_hint="改成命名常量或显式十六进制 mask。",
            tags=("octal", "bit-packing", "c"),
        ),
    )

    def __init__(self, language: str = "c") -> None:
        self.language = language

    def analyze(self, code: str):
        return self._scan_rules(code, self.RULES)
