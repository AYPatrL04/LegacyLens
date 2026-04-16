from __future__ import annotations

from dataclasses import dataclass

from legacylens.models import Severity

from .base import Analyzer, Rule, regex


@dataclass(frozen=True)
class RuleTemplate:
    suffix: str
    title: str
    pattern: str
    severity: Severity
    rationale: str
    tags: tuple[str, ...]
    remediation_hint: str | None = None


STATIC_CONTEXT = "静态行为信号；只基于当前代码片段、文件名和本地目录上下文。"


COMMON_CODE_RULES = (
    RuleTemplate(
        suffix="function",
        title="Function or method boundary",
        pattern=(
            r"\b(def|function|func|fn|sub|proc)\s+[A-Za-z_]\w*"
            r"|\b(public|private|protected|internal|static|async)\b.*\b[A-Za-z_]\w*\s*\([^)]*\)\s*\{"
        ),
        severity=Severity.INFO,
        rationale="这里定义了一个可被调用的行为边界。",
        tags=("behavior", "function"),
        remediation_hint="检查调用方、参数来源和返回值去向。",
    ),
    RuleTemplate(
        suffix="type-boundary",
        title="Type or module boundary",
        pattern=r"\b(class|interface|struct|enum|trait|record|module|namespace|package)\s+[A-Za-z_]\w*",
        severity=Severity.INFO,
        rationale="这里声明了类型、模块、命名空间或包边界。",
        tags=("structure", "type"),
        remediation_hint="结合同目录文件和符号引用判断它承担的职责。",
    ),
    RuleTemplate(
        suffix="branch",
        title="Conditional branch",
        pattern=r"\b(if|elif|else\s+if|switch|case|match|when)\b",
        severity=Severity.LOW,
        rationale="这里会按条件选择不同执行路径。",
        tags=("behavior", "control-flow"),
        remediation_hint="确认哪些输入或状态会触发这个分支。",
    ),
    RuleTemplate(
        suffix="loop",
        title="Loop or iteration",
        pattern=r"\b(for|foreach|while|loop|map|filter|reduce|lapply|sapply)\b",
        severity=Severity.LOW,
        rationale="这里会对集合、范围或条件重复执行逻辑。",
        tags=("behavior", "iteration"),
        remediation_hint="检查循环规模、退出条件和是否修改外部状态。",
    ),
    RuleTemplate(
        suffix="error-flow",
        title="Error handling or exceptional flow",
        pattern=r"\b(try|catch|except|finally|raise|throw|throws|panic!|recover|rescue)\b",
        severity=Severity.MEDIUM,
        rationale="这里处理错误路径或改变异常控制流。",
        tags=("behavior", "error-handling"),
        remediation_hint="追踪异常是被吞掉、转换、记录还是继续抛出。",
    ),
    RuleTemplate(
        suffix="external-io",
        title="External IO, network, or database access",
        pattern=(
            r"\b(open|read|write|readFile|writeFile|fetch|request|requests|http|socket|connect|execute|query)\b"
            r"|\b(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b"
        ),
        severity=Severity.MEDIUM,
        rationale="这里访问文件、网络、数据库或其他外部资源。",
        tags=("behavior", "io", "data-flow"),
        remediation_hint="确认资源位置、失败处理和调用方可见的副作用。",
    ),
    RuleTemplate(
        suffix="dependency",
        title="Dependency import",
        pattern=r"^\s*(import|from|using|use|require|library|source|#include)\b",
        severity=Severity.INFO,
        rationale="这里把外部模块、包或头文件引入当前作用域。",
        tags=("dependency", "structure"),
        remediation_hint="检查被导入符号是否在当前片段中直接参与行为。",
    ),
)


PROFILE_RULES: dict[str, tuple[RuleTemplate, ...]] = {
    "python": (
        RuleTemplate(
            "context-manager",
            "Context manager",
            r"\bwith\s+.+\s+as\s+\w+\s*:",
            Severity.MEDIUM,
            "这里把资源生命周期绑定到代码块。",
            ("resource", "lifecycle"),
            "检查进入和退出块时会打开、提交、关闭或回滚什么资源。",
        ),
        RuleTemplate(
            "file-io",
            "Python file IO",
            r"\bopen\s*\(|\bPath\s*\([^)]*\)\.(read_text|write_text|read_bytes|write_bytes)\s*\(",
            Severity.MEDIUM,
            "这里读取或写入本地文件。",
            ("io", "file"),
            "确认路径来源、编码和异常处理。",
        ),
        RuleTemplate(
            "dynamic-exec",
            "Dynamic execution",
            r"\b(eval|exec)\s*\(",
            Severity.HIGH,
            "这里会执行运行时拼出的代码。",
            ("dynamic-execution", "risk"),
            "确认输入是否可信，并优先替换为显式调用表。",
        ),
        RuleTemplate(
            "decorator",
            "Decorator",
            r"^\s*@[A-Za-z_][\w.]*",
            Severity.INFO,
            "这里在函数或类定义前附加包装逻辑。",
            ("structure", "metadata"),
            "查看装饰器实现，确认它是否改变注册、权限、事务或缓存行为。",
        ),
    ),
    "java": (
        RuleTemplate(
            "annotation",
            "Java annotation",
            r"^\s*@[A-Za-z_]\w*",
            Severity.INFO,
            "这里通过注解把运行时或框架行为挂到代码上。",
            ("metadata", "framework"),
            "检查注解处理器或框架约定。",
        ),
        RuleTemplate(
            "try-with-resources",
            "Try-with-resources",
            r"\btry\s*\([^)]*\)\s*\{",
            Severity.MEDIUM,
            "这里声明的资源会在块退出时自动关闭。",
            ("resource", "lifecycle"),
            "确认资源关闭顺序和异常传播。",
        ),
        RuleTemplate(
            "stream-pipeline",
            "Stream pipeline",
            r"\.stream\s*\(\)|\.(map|filter|flatMap|collect|forEach)\s*\(",
            Severity.LOW,
            "这里用管道式操作转换或消费集合。",
            ("data-flow", "iteration"),
            "检查每一步是否有副作用以及终止操作在哪里。",
        ),
    ),
    "go": (
        RuleTemplate(
            "goroutine",
            "Goroutine launch",
            r"\bgo\s+[A-Za-z_]\w*\s*\(",
            Severity.MEDIUM,
            "这里启动并发执行。",
            ("concurrency", "goroutine"),
            "检查共享状态、取消信号和错误回传路径。",
        ),
        RuleTemplate(
            "channel",
            "Channel operation",
            r"<-|chan\s+",
            Severity.MEDIUM,
            "这里通过 channel 发送、接收或声明并发通信。",
            ("concurrency", "channel"),
            "确认是否可能阻塞以及谁负责关闭 channel。",
        ),
        RuleTemplate(
            "error-check",
            "Explicit error check",
            r"\bif\s+err\s*!=\s*nil\s*\{",
            Severity.MEDIUM,
            "这里检查并处理显式返回的错误。",
            ("error-handling", "control-flow"),
            "确认错误是被包装、记录、重试还是直接返回。",
        ),
        RuleTemplate(
            "defer",
            "Deferred cleanup",
            r"\bdefer\s+",
            Severity.MEDIUM,
            "这里把清理或收尾动作延迟到函数返回前执行。",
            ("resource", "lifecycle"),
            "检查 defer 的执行顺序和闭包捕获变量。",
        ),
    ),
    "csharp": (
        RuleTemplate(
            "async-await",
            "Async flow",
            r"\b(async|await)\b|\bTask<|\bTask\s+",
            Severity.MEDIUM,
            "这里进入异步执行或等待异步结果。",
            ("async", "control-flow"),
            "检查取消、异常传播和调用方是否等待结果。",
        ),
        RuleTemplate(
            "linq",
            "LINQ pipeline",
            r"\.(Where|Select|SelectMany|GroupBy|OrderBy|ToList|FirstOrDefault)\s*\(",
            Severity.LOW,
            "这里用 LINQ 对集合或查询结果做转换。",
            ("data-flow", "iteration"),
            "确认延迟执行边界以及是否触发数据库查询。",
        ),
        RuleTemplate(
            "using-dispose",
            "Using disposal scope",
            r"\busing\s*(var\s+)?\w+\s*=|\busing\s*\(",
            Severity.MEDIUM,
            "这里声明了自动释放资源的作用域。",
            ("resource", "lifecycle"),
            "确认释放对象是否仍被外部引用。",
        ),
    ),
    "rust": (
        RuleTemplate(
            "result-propagation",
            "Result propagation",
            r"\?\s*(;|\)|$)",
            Severity.MEDIUM,
            "这里把错误沿调用栈向上传播。",
            ("error-handling", "result"),
            "检查函数返回类型和上层调用方是否处理该错误。",
        ),
        RuleTemplate(
            "unsafe",
            "Unsafe block",
            r"\bunsafe\s*\{",
            Severity.HIGH,
            "这里绕过部分编译期安全检查。",
            ("unsafe", "memory"),
            "确认指针、别名和生命周期不变量由代码显式维护。",
        ),
        RuleTemplate(
            "borrow",
            "Borrow or mutable reference",
            r"&mut\s+|&[A-Za-z_]\w*",
            Severity.INFO,
            "这里借用值或传递可变引用。",
            ("ownership", "data-flow"),
            "检查借用持续时间和是否会影响调用方状态。",
        ),
        RuleTemplate(
            "trait-impl",
            "Trait implementation",
            r"\bimpl\s+([A-Za-z_]\w*\s+for\s+)?[A-Za-z_]\w*",
            Severity.INFO,
            "这里为类型提供方法或 trait 行为。",
            ("structure", "type"),
            "查看 trait 约束和被实现类型的调用位置。",
        ),
    ),
    "r": (
        RuleTemplate(
            "assignment",
            "R assignment",
            r"<-|->|<<-",
            Severity.INFO,
            "这里修改对象绑定或向外层环境赋值。",
            ("data-flow", "assignment"),
            "确认赋值目标所在环境，尤其是 `<<-`。",
        ),
        RuleTemplate(
            "pipeline",
            "R pipeline",
            r"%>%|\|>",
            Severity.LOW,
            "这里把数据逐步传入后续转换。",
            ("data-flow", "pipeline"),
            "检查每个管道阶段的数据形状是否改变。",
        ),
        RuleTemplate(
            "data-io",
            "R data IO",
            r"\b(read\.csv|readRDS|write\.csv|saveRDS|read_excel)\s*\(",
            Severity.MEDIUM,
            "这里读取或写入分析数据。",
            ("io", "data"),
            "确认路径、列类型和缺失值处理。",
        ),
    ),
    "javascript": (
        RuleTemplate(
            "async-promise",
            "Async or promise flow",
            r"\basync\b|\bawait\b|\.then\s*\(|new\s+Promise\s*\(",
            Severity.MEDIUM,
            "这里创建或等待异步执行结果。",
            ("async", "control-flow"),
            "检查 rejected promise 是否被捕获。",
        ),
        RuleTemplate(
            "module-export",
            "Module export",
            r"\bexport\s+|module\.exports|exports\.",
            Severity.INFO,
            "这里把符号暴露给其他模块。",
            ("dependency", "api"),
            "检查导出符号在项目内的引用位置。",
        ),
        RuleTemplate(
            "dom-or-fetch",
            "Browser or HTTP boundary",
            r"\b(fetch|document\.|window\.|addEventListener)\b",
            Severity.MEDIUM,
            "这里访问浏览器对象或发起 HTTP 请求。",
            ("io", "web"),
            "确认事件来源、请求目标和错误处理。",
        ),
    ),
    "typescript": (
        RuleTemplate(
            "type-contract",
            "Type contract",
            r"\b(type|interface)\s+[A-Za-z_]\w*|:\s*[A-Za-z_][\w<>| ]+",
            Severity.INFO,
            "这里声明或使用 TypeScript 类型约束。",
            ("structure", "type"),
            "检查类型是否只约束编译期，运行时是否另有校验。",
        ),
    ),
    "sql": (
        RuleTemplate(
            "select",
            "Read query",
            r"\bSELECT\b.+\bFROM\b",
            Severity.MEDIUM,
            "这里从表、视图或子查询读取数据。",
            ("database", "read"),
            "检查过滤条件、连接条件和结果规模。",
        ),
        RuleTemplate(
            "mutation",
            "Data mutation",
            r"\b(INSERT|UPDATE|DELETE|MERGE)\b",
            Severity.HIGH,
            "这里会修改数据库数据。",
            ("database", "write"),
            "确认事务边界、WHERE 条件和回滚策略。",
        ),
        RuleTemplate(
            "join",
            "Join boundary",
            r"\bJOIN\b",
            Severity.MEDIUM,
            "这里把多个数据源按条件合并。",
            ("database", "data-flow"),
            "检查 join 条件是否唯一以及是否会放大结果集。",
        ),
    ),
    "shell": (
        RuleTemplate(
            "pipeline",
            "Shell pipeline",
            r"\|",
            Severity.LOW,
            "这里把一个命令的输出传给下一个命令。",
            ("process", "pipeline"),
            "检查失败码是否会被保留，以及每步处理的数据格式。",
        ),
        RuleTemplate(
            "env",
            "Environment variable access",
            r"\$[A-Za-z_]\w*|%[A-Za-z_]\w*%",
            Severity.INFO,
            "这里读取或展开环境变量。",
            ("configuration", "environment"),
            "确认变量是否必须由调用环境提供。",
        ),
        RuleTemplate(
            "subprocess",
            "External command execution",
            r"\b(curl|wget|ssh|scp|docker|kubectl|python|node|java)\b",
            Severity.MEDIUM,
            "这里调用外部进程或工具。",
            ("process", "io"),
            "确认参数来源、退出码处理和输出消费方。",
        ),
    ),
    "powershell": (
        RuleTemplate(
            "cmdlet-pipeline",
            "PowerShell pipeline",
            r"\|\s*[A-Za-z]+-[A-Za-z]+",
            Severity.LOW,
            "这里把对象流传给后续 cmdlet。",
            ("process", "pipeline"),
            "检查每一步接收的是对象还是字符串。",
        ),
    ),
    "jvm": (
        RuleTemplate(
            "synchronized",
            "Synchronized region",
            r"\bsynchronized\b",
            Severity.MEDIUM,
            "这里保护并发访问或声明同步方法。",
            ("concurrency", "lock"),
            "检查锁对象、临界区范围和死锁风险。",
        ),
    ),
    "dotnet": (
        RuleTemplate(
            "attribute",
            "Attribute metadata",
            r"^\s*\[[A-Za-z_]\w*",
            Severity.INFO,
            "这里通过 attribute 影响框架、序列化、测试或运行时行为。",
            ("metadata", "framework"),
            "查看 attribute 的消费者是谁。",
        ),
    ),
    "functional": (
        RuleTemplate(
            "pattern-match",
            "Pattern matching",
            r"\b(match|case|receive)\b|=>",
            Severity.LOW,
            "这里按模式选择执行分支。",
            ("control-flow", "pattern-match"),
            "确认未覆盖分支和默认分支。",
        ),
    ),
    "scripting": (
        RuleTemplate(
            "dynamic-call",
            "Dynamic call or include",
            r"\b(eval|send|method_missing|include|require|source)\b",
            Severity.MEDIUM,
            "这里可能在运行时决定调用或加载目标。",
            ("dynamic-execution", "dependency"),
            "确认目标是否固定以及输入是否可信。",
        ),
    ),
    "objc": (
        RuleTemplate(
            "message-send",
            "Objective-C message send",
            r"\[[A-Za-z_]\w*\s+[A-Za-z_]\w*",
            Severity.INFO,
            "这里向对象发送消息。",
            ("behavior", "method-call"),
            "检查接收者类型和 selector 是否由运行时决定。",
        ),
    ),
    "component": (
        RuleTemplate(
            "component-script",
            "Component script block",
            r"<script\b",
            Severity.INFO,
            "这里进入组件的脚本逻辑。",
            ("ui", "component"),
            "检查模板中是否引用这些状态或方法。",
        ),
    ),
    "markup": (
        RuleTemplate(
            "form-or-script",
            "Markup behavior boundary",
            r"<(form|script|input|button|a)\b",
            Severity.LOW,
            "这里定义交互入口、脚本入口或导航入口。",
            ("ui", "boundary"),
            "检查事件处理器、表单提交目标和脚本来源。",
        ),
    ),
    "style": (
        RuleTemplate(
            "selector",
            "Style selector",
            r"^[\s.#:[A-Za-z0-9_-].*\{",
            Severity.INFO,
            "这里定义样式命中的选择器范围。",
            ("ui", "style"),
            "确认选择器是否过宽，以及是否影响组件外部。",
        ),
    ),
    "data-file": (
        RuleTemplate(
            "key-value",
            "Configuration entry",
            r"^\s*[\w.-]+\s*[:=]",
            Severity.INFO,
            "这里定义配置键和值。",
            ("configuration", "data"),
            "检查该键由哪个运行时、工具或服务读取。",
        ),
    ),
    "docs": (
        RuleTemplate(
            "link-or-code",
            "Documentation reference",
            r"`[^`]+`|\[[^\]]+\]\([^)]+\)",
            Severity.INFO,
            "这里引用代码标识符、命令或外部文档。",
            ("documentation", "reference"),
            "检查引用是否与当前实现保持一致。",
        ),
    ),
    "dockerfile": (
        RuleTemplate(
            "image-layer",
            "Container image layer",
            r"^\s*(FROM|RUN|COPY|ADD|CMD|ENTRYPOINT|ENV|ARG)\b",
            Severity.MEDIUM,
            "这里定义镜像层、启动命令或构建参数。",
            ("container", "build"),
            "检查构建上下文、缓存层和运行时入口。",
        ),
    ),
}


LANGUAGE_PROFILES: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java", "jvm"),
    "go": ("go",),
    "csharp": ("csharp", "dotnet"),
    "rust": ("rust",),
    "r": ("r",),
    "javascript": ("javascript",),
    "typescript": ("typescript", "javascript"),
    "php": ("scripting",),
    "ruby": ("scripting",),
    "kotlin": ("jvm",),
    "swift": (),
    "scala": ("jvm", "functional"),
    "sql": ("sql",),
    "shell": ("shell",),
    "batch": ("shell",),
    "powershell": ("powershell", "shell"),
    "dart": ("javascript",),
    "lua": ("scripting",),
    "perl": ("scripting",),
    "haskell": ("functional",),
    "elixir": ("functional",),
    "erlang": ("functional",),
    "clojure": ("functional", "jvm"),
    "groovy": ("jvm", "scripting"),
    "fsharp": ("functional", "dotnet"),
    "vb": ("dotnet",),
    "julia": ("scripting",),
    "objective-c": ("objc",),
    "objective-cpp": ("objc",),
    "html": ("markup",),
    "css": ("style",),
    "scss": ("style",),
    "sass": ("style",),
    "less": ("style",),
    "vue": ("component", "markup", "javascript"),
    "svelte": ("component", "markup", "javascript"),
    "json": ("data-file",),
    "jsonc": ("data-file",),
    "yaml": ("data-file",),
    "toml": ("data-file",),
    "xml": ("markup", "data-file"),
    "markdown": ("docs",),
    "dockerfile": ("dockerfile",),
}


NON_CODE_LANGUAGES = {
    "css",
    "scss",
    "sass",
    "less",
    "json",
    "jsonc",
    "yaml",
    "toml",
    "xml",
    "markdown",
    "dockerfile",
    "sql",
}


class MainstreamAnalyzer(Analyzer):
    """Profile-based analyzer for supported non-legacy languages."""

    def __init__(self, language: str, include_common: bool | None = None) -> None:
        self.language = language
        profiles = LANGUAGE_PROFILES.get(language, ())
        templates: list[RuleTemplate] = []
        for profile in profiles:
            templates.extend(PROFILE_RULES.get(profile, ()))
        if include_common is True or (include_common is None and language not in NON_CODE_LANGUAGES):
            templates.extend(COMMON_CODE_RULES)
        self._rules = tuple(_rule_from_template(language, template) for template in _dedupe(templates))

    def analyze(self, code: str):
        return self._scan_rules(code, self._rules)


def mainstream_analyzer(language: str) -> MainstreamAnalyzer | None:
    if language not in LANGUAGE_PROFILES:
        return None
    return MainstreamAnalyzer(language)


def _dedupe(templates: list[RuleTemplate]) -> list[RuleTemplate]:
    seen: set[str] = set()
    deduped: list[RuleTemplate] = []
    for template in templates:
        if template.suffix in seen:
            continue
        seen.add(template.suffix)
        deduped.append(template)
    return deduped


def _rule_from_template(language: str, template: RuleTemplate) -> Rule:
    return Rule(
        rule_id=f"{language}.{template.suffix}",
        title=template.title,
        pattern=regex(template.pattern),
        severity=template.severity,
        rationale=template.rationale,
        historical_context=STATIC_CONTEXT,
        remediation_hint=template.remediation_hint,
        tags=template.tags + (language,),
    )
