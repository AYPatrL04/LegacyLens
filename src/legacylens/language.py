from __future__ import annotations

from pathlib import Path


LANGUAGE_ALIASES = {
    "asm": "asm",
    "assembly": "asm",
    "c": "c",
    "c++": "cpp",
    "c#": "csharp",
    "clj": "clojure",
    "cljs": "clojure",
    "cob": "cobol",
    "cobol": "cobol",
    "cs": "csharp",
    "csharp": "csharp",
    "cpp": "cpp",
    "cxx": "cpp",
    "erl": "erlang",
    "ex": "elixir",
    "exs": "elixir",
    "f": "fortran",
    "f#": "fsharp",
    "f77": "fortran",
    "fs": "fsharp",
    "fortran": "fortran",
    "fortran77": "fortran",
    "go": "go",
    "golang": "go",
    "javascriptreact": "javascript",
    "js": "javascript",
    "jsx": "javascript",
    "kt": "kotlin",
    "node": "javascript",
    "objectivec": "objective-c",
    "objc": "objective-c",
    "pl": "perl",
    "ps": "powershell",
    "ps1": "powershell",
    "py": "python",
    "python": "python",
    "r": "r",
    "rb": "ruby",
    "rs": "rust",
    "shellscript": "shell",
    "sh": "shell",
    "ts": "typescript",
    "tsx": "typescript",
    "typescriptreact": "typescript",
}

EXTENSION_LANGUAGE = {
    ".asm": "asm",
    ".s": "asm",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cbl": "cobol",
    ".cob": "cobol",
    ".cpy": "cobol",
    ".f": "fortran",
    ".f77": "fortran",
    ".for": "fortran",
    ".ftn": "fortran",
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".rs": "rust",
    ".r": "r",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".php": "php",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ksh": "shell",
    ".fish": "shell",
    ".bat": "batch",
    ".cmd": "batch",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".psd1": "powershell",
    ".dart": "dart",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".cljc": "clojure",
    ".groovy": "groovy",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".fsi": "fsharp",
    ".vb": "vb",
    ".jl": "julia",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".json": "json",
    ".jsonc": "jsonc",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".dockerfile": "dockerfile",
}


def normalize_language(language: str | None) -> str | None:
    if not language:
        return None
    cleaned = language.strip().lower().replace(" ", "")
    return LANGUAGE_ALIASES.get(cleaned, cleaned)


def detect_language(code: str, file_name: str | None = None, explicit: str | None = None) -> str:
    normalized = normalize_language(explicit)
    if normalized:
        return normalized

    if file_name:
        path = Path(file_name)
        if path.name.lower() == "dockerfile":
            return "dockerfile"
        suffix = path.suffix.lower()
        if suffix in EXTENSION_LANGUAGE:
            return EXTENSION_LANGUAGE[suffix]

    upper = code.upper()
    if " IDENTIFICATION DIVISION" in upper or "\n       IDENTIFICATION DIVISION" in upper:
        return "cobol"
    if " PROCEDURE DIVISION" in upper or " WORKING-STORAGE SECTION" in upper:
        return "cobol"
    if " COMMON " in upper or "\n      COMMON" in upper or "\n      PROGRAM " in upper:
        return "fortran"
    if "package main" in code or "\nfunc " in code:
        return "go"
    if "def " in code or "import " in code and ":" in code:
        return "python"
    if "public class " in code or "private class " in code or "\nclass " in code and ";" in code:
        return "java"
    if "using System" in code or "namespace " in code and "{" in code:
        return "csharp"
    if "\nfn " in code or "let mut " in code or "impl " in code:
        return "rust"
    if "<-" in code and any(token in code for token in ("function", "library(", "data.frame")):
        return "r"
    if "#include" in code or "->" in code or "printf(" in code:
        return "c"
    if "function " in code or "const " in code or "let " in code:
        return "javascript"
    if any(token in upper for token in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE")):
        return "sql"
    if any(token in upper for token in (" MOV ", " JMP ", " PUSH ", " POP ", " SHL ", " ROR ")):
        return "asm"
    return "unknown"
