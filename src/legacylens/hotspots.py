from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import AnalysisRequest, ProjectContext

HOTSPOT_CACHE_TTL_SECONDS = 300.0
HOTSPOT_MIN_REPEAT = 2
HOTSPOT_MAX_SYMBOLS = 24
CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "catch",
    "new",
    "throw",
    "elif",
    "def",
    "class",
}


@dataclass(frozen=True)
class _FileHotspotEntry:
    built_at: float
    language: str | None
    lines: tuple[str, ...]
    symbols: dict[str, tuple[int, ...]]
    path: str


_HOTSPOT_CACHE: dict[str, _FileHotspotEntry] = {}
_HOTSPOT_CACHE_LOCK = threading.Lock()


def prewarm_file_hotspots(file_name: str | None, code: str, language: str | None = None) -> bool:
    resolved = _resolve_file_key(file_name)
    if not resolved or not code.strip():
        return False
    lines = tuple(code.splitlines())
    symbol_lines = _extract_call_hotspots(lines)
    entry = _FileHotspotEntry(
        built_at=time.monotonic(),
        language=language,
        lines=lines,
        symbols=symbol_lines,
        path=resolved,
    )
    with _HOTSPOT_CACHE_LOCK:
        _HOTSPOT_CACHE[resolved] = entry
    return True


def augment_context_with_hotspots(request: AnalysisRequest, context: ProjectContext | None) -> ProjectContext | None:
    resolved = _resolve_file_key(request.file_name)
    if not resolved or request.cursor_line is None:
        return context
    entry = _get_entry(resolved)
    if entry is None:
        return context
    symbol = _symbol_at_cursor(entry.lines, request.cursor_line, request.cursor_column)
    if not symbol:
        return context
    occurrences = entry.symbols.get(symbol)
    if not occurrences or len(occurrences) < HOTSPOT_MIN_REPEAT:
        return context

    references = list(context.symbol_references) if context else []
    current_file_path = request.file_name or context.current_file if context else request.file_name
    for line_no in occurrences[:8]:
        if line_no == request.cursor_line or any(
            ref.get("path") == current_file_path and ref.get("line") == line_no for ref in references
        ):
            continue
        line_text = entry.lines[line_no - 1].strip() if 1 <= line_no <= len(entry.lines) else ""
        references.append(
            {
                "symbol": symbol,
                "path": current_file_path or resolved,
                "line": line_no,
                "text": line_text[:160],
            }
        )

    lines_text = ", ".join(str(line_no) for line_no in occurrences[:8])
    note = f"Current-file hotspot: `{symbol}` appears {len(occurrences)} times at lines {lines_text}."
    notes = list(context.notes) if context else []
    if note not in notes:
        notes.append(note)

    if context is None:
        return ProjectContext(
            scope=request.context_scope,
            current_file=current_file_path,
            symbol_references=references,
            notes=notes,
        )
    return ProjectContext(
        scope=context.scope,
        root=context.root,
        current_directory=context.current_directory,
        current_file=context.current_file,
        files=list(context.files),
        related_files=list(context.related_files),
        symbol_references=references,
        notes=notes,
    )


def _get_entry(resolved_path: str) -> _FileHotspotEntry | None:
    now = time.monotonic()
    with _HOTSPOT_CACHE_LOCK:
        entry = _HOTSPOT_CACHE.get(resolved_path)
        if entry is None:
            return None
        if now - entry.built_at > HOTSPOT_CACHE_TTL_SECONDS:
            _HOTSPOT_CACHE.pop(resolved_path, None)
            return None
        return entry


def _extract_call_hotspots(lines: tuple[str, ...]) -> dict[str, tuple[int, ...]]:
    hits: dict[str, list[int]] = {}
    for index, line in enumerate(lines, start=1):
        for match in CALL_PATTERN.finditer(line):
            symbol = match.group(1)
            if symbol.lower() in KEYWORDS:
                continue
            hits.setdefault(symbol, []).append(index)
    ranked = sorted(
        ((symbol, tuple(line_numbers)) for symbol, line_numbers in hits.items() if len(line_numbers) >= HOTSPOT_MIN_REPEAT),
        key=lambda item: (-len(item[1]), item[0].lower()),
    )
    return dict(ranked[:HOTSPOT_MAX_SYMBOLS])


def _symbol_at_cursor(lines: tuple[str, ...], cursor_line: int, cursor_column: int | None) -> str | None:
    if not (1 <= cursor_line <= len(lines)):
        return None
    line = lines[cursor_line - 1]
    if not line:
        return None
    if cursor_column is None:
        for match in CALL_PATTERN.finditer(line):
            symbol = match.group(1)
            if symbol.lower() not in KEYWORDS:
                return symbol
        return None
    index = max(0, min(len(line) - 1, cursor_column - 1))
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", line):
        if match.start() <= index < match.end():
            symbol = match.group(0)
            return None if symbol.lower() in KEYWORDS else symbol
    return None


def _resolve_file_key(file_name: str | None) -> str | None:
    if not file_name:
        return None
    path = Path(file_name)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())
