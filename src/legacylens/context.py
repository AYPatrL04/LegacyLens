from __future__ import annotations

import os
import re
from pathlib import Path

from .language import EXTENSION_LANGUAGE
from .models import AnalysisRequest, ProjectContext

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "out",
    "target",
}
EXCLUDED_SUFFIXES = {
    ".7z",
    ".bin",
    ".class",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpg",
    ".jpeg",
    ".lock",
    ".obj",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".zip",
}
PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle")
REFERENCE_EXTENSIONS = set(EXTENSION_LANGUAGE) | {".inc", ".txt"}
LANGUAGE_EXTENSIONS: dict[str, set[str]] = {}
for _suffix, _language in EXTENSION_LANGUAGE.items():
    LANGUAGE_EXTENSIONS.setdefault(_language, set()).add(_suffix)
LANGUAGE_EXTENSIONS.setdefault("asm", set()).add(".inc")
LANGUAGE_EXTENSIONS.setdefault("c", set()).update({".h", ".inc"})
LANGUAGE_EXTENSIONS.setdefault("cpp", set()).update({".h", ".hpp", ".hh", ".inc"})
LANGUAGE_EXTENSIONS.setdefault("objective-c", set()).update({".h"})
LANGUAGE_EXTENSIONS.setdefault("objective-cpp", set()).update({".h", ".hpp"})
LANGUAGE_EXTENSIONS.setdefault("fortran", set()).add(".inc")
KEYWORDS = {
    "and",
    "char",
    "common",
    "continue",
    "do",
    "else",
    "end",
    "for",
    "goto",
    "if",
    "int",
    "long",
    "or",
    "perform",
    "return",
    "short",
    "signed",
    "static",
    "stop",
    "then",
    "unsigned",
    "void",
    "while",
}


def build_project_context(request: AnalysisRequest, language: str) -> ProjectContext | None:
    if request.context_scope == "none":
        return None

    current_file = _resolve_file(request.file_name)
    current_directory = current_file.parent if current_file else Path.cwd()
    project_root = _resolve_root(request.project_root, current_file, current_directory)
    scope_root = project_root if request.context_scope == "project" else current_directory
    if scope_root is None or not scope_root.exists():
        return ProjectContext(scope=request.context_scope, notes=["No readable context root was found."])

    files = _collect_files(scope_root, limit=120 if request.context_scope == "project" else 60)
    related_files = _related_files(files, current_file, language)
    symbols = _extract_focus_symbols(request.code, request.relative_cursor_line())
    references = _find_symbol_references(scope_root, symbols, current_file, language, limit=12)
    notes: list[str] = []
    if symbols:
        notes.append(f"Focus symbols: {', '.join(symbols[:8])}.")
    if len(files) >= (120 if request.context_scope == "project" else 60):
        notes.append("Context file list was truncated.")

    return ProjectContext(
        scope=request.context_scope,
        root=_safe_relative_or_absolute(scope_root),
        current_directory=_safe_relative_or_absolute(current_directory),
        current_file=_safe_relative_or_absolute(current_file) if current_file else request.file_name,
        files=[_relative_path(path, scope_root) for path in files],
        related_files=[_relative_path(path, scope_root) for path in related_files],
        symbol_references=references,
        notes=notes,
    )


def _resolve_file(file_name: str | None) -> Path | None:
    if not file_name:
        return None
    path = Path(file_name)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _resolve_root(project_root: str | None, current_file: Path | None, current_directory: Path) -> Path:
    if project_root:
        root = Path(project_root)
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    start = current_file.parent if current_file else current_directory
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate.resolve()
    return current_directory.resolve()


def _collect_files(root: Path, limit: int) -> list[Path]:
    collected: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names if name not in EXCLUDED_DIRS)
        for file_name in sorted(file_names):
            path = Path(current_root) / file_name
            if _should_skip(path):
                continue
            collected.append(path.resolve())
            if len(collected) >= limit:
                return collected
    return collected


def _related_files(files: list[Path], current_file: Path | None, language: str) -> list[Path]:
    if not current_file:
        return files[:12]
    current_suffix = current_file.suffix.lower()
    language_extensions = LANGUAGE_EXTENSIONS.get(language, {current_suffix})
    related: list[Path] = []
    for path in files:
        if path == current_file:
            continue
        suffix = path.suffix.lower()
        if suffix == current_suffix or suffix in language_extensions:
            related.append(path)
    config_names = {"makefile", "cmakelists.txt", "package.json", "pyproject.toml", "readme.md"}
    for path in files:
        if path.name.lower() in config_names and path not in related:
            related.append(path)
    return related[:16]


def _find_symbol_references(
    root: Path,
    symbols: list[str],
    current_file: Path | None,
    language: str,
    limit: int,
) -> list[dict[str, str | int]]:
    if not symbols:
        return []
    references: list[dict[str, str | int]] = []
    filtered_symbols = _reference_symbols(symbols)
    patterns = [(symbol, re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)) for symbol in filtered_symbols[:8]]
    allowed_extensions = LANGUAGE_EXTENSIONS.get(language, REFERENCE_EXTENSIONS)
    files = _collect_files(root, limit=300)
    for path in files:
        if path == current_file or path.suffix.lower() not in allowed_extensions:
            continue
        try:
            if path.stat().st_size > 200_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines, start=1):
            matched = next((symbol for symbol, pattern in patterns if pattern.search(line)), None)
            if not matched:
                continue
            references.append(
                {
                    "symbol": matched,
                    "path": _relative_path(path, root),
                    "line": index,
                    "text": line.strip()[:160],
                }
            )
            if len(references) >= limit:
                return references
    return references


def _extract_focus_symbols(code: str, cursor_line: int | None) -> list[str]:
    lines = code.splitlines()
    focus_parts: list[str] = []
    if cursor_line and 1 <= cursor_line <= len(lines):
        focus_parts.append(lines[cursor_line - 1])
        if cursor_line > 1:
            focus_parts.append(lines[cursor_line - 2])
        if cursor_line < len(lines):
            focus_parts.append(lines[cursor_line])
    focus_parts.append("\n".join(lines[:80]))

    seen: set[str] = set()
    symbols: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", "\n".join(focus_parts)):
        normalized = token.lower()
        if normalized in KEYWORDS or normalized in seen:
            continue
        seen.add(normalized)
        symbols.append(token)
        if len(symbols) >= 12:
            break
    return symbols


def _reference_symbols(symbols: list[str]) -> list[str]:
    strong = [symbol for symbol in symbols if symbol.isupper() or "_" in symbol or len(symbol) >= 6]
    return strong or symbols[:4]


def _should_skip(path: Path) -> bool:
    return path.suffix.lower() in EXCLUDED_SUFFIXES or any(part in EXCLUDED_DIRS for part in path.parts)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_relative_or_absolute(path: Path) -> str:
    try:
        return path.resolve().as_posix()
    except OSError:
        return path.as_posix()
