from __future__ import annotations

from legacylens.models import Severity

from .base import Analyzer, Rule, regex


class AssemblyAnalyzer(Analyzer):
    language = "asm"

    RULES = (
        Rule(
            rule_id="asm.jump",
            title="Manual branch or loop jump",
            pattern=regex(r"\b(JMP|JE|JNE|JNZ|JZ|JG|JL|JA|JB|LOOP)\b"),
            severity=Severity.MEDIUM,
            rationale="这条指令会根据标签或标志位转移控制流。",
            historical_context="汇编通常直接用分支指令表达循环和分派。",
            remediation_hint="把分支与前面的比较或标志位修改配对阅读。",
            tags=("jump", "control-flow", "asm"),
        ),
        Rule(
            rule_id="asm.bit-shift",
            title="Bit shift or rotate",
            pattern=regex(r"\b(SHL|SHR|SAR|SAL|ROL|ROR|RCL|RCR)\b"),
            severity=Severity.LOW,
            rationale="这里移动或旋转 bit，用于打包、提取或快速计算。",
            historical_context="位移和旋转常用于 flag、算术捷径和硬件寄存器协议。",
            remediation_hint="标出被操作的 bit 位或寄存器字段。",
            tags=("bit-packing", "flags", "asm"),
        ),
        Rule(
            rule_id="asm.raw-bytes",
            title="Embedded raw bytes",
            pattern=regex(r"^\s*(DB|\.BYTE|BYTE)\b"),
            severity=Severity.MEDIUM,
            rationale="源码直接嵌入原始字节。",
            historical_context="原始字节常表示查表数据、补丁指令或设备协议数据。",
            remediation_hint="先判断这些字节是数据、查表还是指令流。",
            tags=("raw-bytes", "packed-data", "asm"),
        ),
    )

    def analyze(self, code: str):
        return self._scan_rules(code, self.RULES)
