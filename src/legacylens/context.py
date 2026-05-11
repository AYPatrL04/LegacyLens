from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
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
DIRECTORY_FILE_LIMIT = 60
PROJECT_FILE_LIMIT = 120
REFERENCE_FILE_LIMIT = 300
TEXT_CACHE_MAX_BYTES = 120_000
CONTEXT_CACHE_TTL_SECONDS = 45.0


@dataclass
class _ProjectScanCacheEntry:
    built_at: float
    files: list[Path]
    text_by_path: dict[Path, list[str]]


_PROJECT_SCAN_CACHE: dict[str, _ProjectScanCacheEntry] = {}
_PROJECT_SCAN_CACHE_LOCK = threading.Lock()


def build_project_context(request: AnalysisRequest, language: str) -> ProjectContext | None:
    if request.context_scope == "none":
        return None

    current_file = _resolve_file(request.file_name)
    current_directory = current_file.parent if current_file else Path.cwd()
    project_root = _resolve_root(request.project_root, current_file, current_directory)
    scope_root = project_root if request.context_scope == "project" else current_directory
    if scope_root is None or not scope_root.exists():
        return ProjectContext(scope=request.context_scope, notes=["No readable context root was found."])

    file_limit = PROJECT_FILE_LIMIT if request.context_scope == "project" else DIRECTORY_FILE_LIMIT
    scan = _scan_project(scope_root, limit=max(file_limit, REFERENCE_FILE_LIMIT))
    files = scan.files[:file_limit]
    related_files = _related_files(files, current_file, language)
    symbols = _extract_focus_symbols(request.code, request.relative_cursor_line())
    references = _find_symbol_references(
        scope_root,
        scan.files,
        scan.text_by_path,
        symbols,
        current_file,
        language,
        limit=12,
    )
    notes: list[str] = []
    if symbols:
        notes.append(f"Focus symbols: {', '.join(symbols[:8])}.")
    if len(scan.files) >= file_limit:
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


def prewarm_project_context(project_root: str | Path | None) -> bool:
    root = _resolve_root_path(project_root)
    if root is None or not root.exists():
        return False
    _scan_project(root, limit=REFERENCE_FILE_LIMIT, force_refresh=True)
    return True


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


def _resolve_root_path(project_root: str | Path | None) -> Path | None:
    if project_root is None:
        return None
    root = Path(project_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    try:
        return root.resolve()
    except OSError:
        return root.absolute()


def _scan_project(root: Path, limit: int, force_refresh: bool = False) -> _ProjectScanCacheEntry:
    cache_key = _safe_relative_or_absolute(root)
    now = time.monotonic()
    with _PROJECT_SCAN_CACHE_LOCK:
        cached = _PROJECT_SCAN_CACHE.get(cache_key)
        if (
            cached is not None
            and not force_refresh
            and now - cached.built_at <= CONTEXT_CACHE_TTL_SECONDS
            and len(cached.files) >= limit
        ):
            return cached
        if not force_refresh:
            ancestor = _ancestor_cache_locked(root, limit=limit, now=now)
            if ancestor is not None:
                return ancestor

    files = _collect_files(root, limit=limit)
    text_by_path: dict[Path, list[str]] = {}
    for path in files:
        lines = _read_text_lines(path)
        if lines is not None:
            text_by_path[path] = lines

    refreshed = _ProjectScanCacheEntry(built_at=now, files=files, text_by_path=text_by_path)
    with _PROJECT_SCAN_CACHE_LOCK:
        _PROJECT_SCAN_CACHE[cache_key] = refreshed
    return refreshed


def _ancestor_cache_locked(root: Path, limit: int, now: float) -> _ProjectScanCacheEntry | None:
    root_text = _safe_relative_or_absolute(root)
    best_match: _ProjectScanCacheEntry | None = None
    best_prefix_length = -1
    for cached_root, cached in _PROJECT_SCAN_CACHE.items():
        if now - cached.built_at > CONTEXT_CACHE_TTL_SECONDS or len(cached.files) < limit:
            continue
        if root_text == cached_root or not root_text.startswith(f"{cached_root}/"):
            continue
        prefix_length = len(cached_root)
        if prefix_length > best_prefix_length:
            best_match = _slice_cache_for_descendant(root, cached)
            best_prefix_length = prefix_length
    return best_match


def _slice_cache_for_descendant(root: Path, cached: _ProjectScanCacheEntry) -> _ProjectScanCacheEntry:
    descendant_files = [path for path in cached.files if path == root or root in path.parents]
    descendant_text = {path: lines for path, lines in cached.text_by_path.items() if path in descendant_files}
    return _ProjectScanCacheEntry(built_at=cached.built_at, files=descendant_files, text_by_path=descendant_text)


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
    files: list[Path],
    text_by_path: dict[Path, list[str]],
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
    for path in files:
        if path == current_file or path.suffix.lower() not in allowed_extensions:
            continue
        lines = text_by_path.get(path)
        if lines is None:
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


def _read_text_lines(path: Path) -> list[str] | None:
    try:
        if path.stat().st_size > TEXT_CACHE_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


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
