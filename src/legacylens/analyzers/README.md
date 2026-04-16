# Legacy Lens Analyzers

`src/legacylens/analyzers` 只负责本地静态命中。它不调用网络、不调用 Ollama，也不读取目录上下文。目录和模型相关逻辑在 engine、context 和 llm 层完成。

## Motivation

Analyzer 的目标不是完整解析每门语言，而是给解释器提供可靠锚点：

- 哪一行有明确行为特征。
- 这个特征属于控制流、数据流、资源生命周期、并发、错误处理还是数据布局。
- 后续解释可以引用哪些具体行，而不是凭模型自由发挥。

因此规则文本应该短：说明“这行做了什么”和“下一步看哪里”，不要在 analyzer 中写长篇背景故事。

## Structure

专用 analyzer：

- `c_like.py`: C/C++ 遗留写法，例如 `goto`、`union`、bit mask、宏和八进制字面量。
- `fortran.py`: Fortran 77 风格结构，例如 `COMMON`、`EQUIVALENCE`、computed GOTO、arithmetic IF、label DO。
- `cobol.py`: COBOL 控制流和记录布局，例如 `PERFORM THRU`、`ALTER`、`REDEFINES`、`OCCURS DEPENDING ON`。
- `assembly.py`: 汇编跳转、位操作和原始字节。

主流语言 analyzer：

- `mainstream.py`: profile-based analyzer。
- 每门语言仍然输出自己的规则 ID，例如 `python.file-io`、`java.stream-pipeline`、`go.goroutine`、`csharp.async-await`、`rust.result-propagation`、`r.pipeline`。
- 多门语言可以共享 profile，例如 JVM、.NET、shell、functional、data-file，但对外不再暴露 `generic.*` 规则。

兜底 analyzer：

- `unknown.py`: 在无法识别语言时组合遗留 analyzer 和 mainstream common rules。

## Usage

Analyzer 通常不单独作为命令运行，而是由 `LegacyLensEngine` 根据语言识别结果选择：

- C/C++、Fortran、COBOL、ASM 进入专用 analyzer。
- Python、Java、Go、C#、Rust、R 等现代语言进入 `MainstreamAnalyzer`。
- 无法识别的代码进入 `UnknownAnalyzer`。

可以通过 CLI 查看 analyzer 输出对最终解释的影响：

```powershell
$env:PYTHONPATH = "src"
@'
int flags = 0;
flags = flags | 001;
'@ | python -m legacylens analyze - --language c --cursor-line 2 --format json
```

JSON 中的 `findings[].rule_id`、`span`、`rationale` 和 `tags` 就是 analyzer 层产物。

## Rule Style

规则应保持这些约束：

- `rule_id`: 使用语言前缀或明确家族前缀，避免含糊的 `generic.*`。
- `title`: 短标签，用于 UI 和日志。
- `rationale`: 一句话解释该行的实际行为。
- `historical_context`: 只保留必要背景；现代语言 profile 使用统一静态上下文说明。
- `remediation_hint`: 一句话说明下一步检查点。
- `tags`: 用于事实库检索和结果分组，应该稳定。

## Adding A Rule

优先级：

1. 如果是 COBOL、Fortran、C/C++、ASM 的特定遗留结构，放进对应专用 analyzer。
2. 如果是 Python、Java、Go、C#、Rust、R 等现代语言的典型行为，放进 `PROFILE_RULES` 对应 profile。
3. 如果是多门语言共享的行为，放进 `COMMON_CODE_RULES` 或一个明确命名的 profile。
4. 如果只是猜测性风格判断，不要加规则。Analyzer 应该命中可见语法或明确调用。

添加规则后至少补一个测试，验证 rule id 和命中行。

## Privacy

Analyzer 层完全本地、纯静态：

- 不发网络请求。
- 不读取远程数据。
- 不调用模型。
- 不扫描 analyzer 输入以外的目录。

它只接收 engine 传入的代码字符串，并返回本地构造的 finding 列表。
