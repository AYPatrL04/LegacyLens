from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_FILENAMES = (".legacylens.local.json", ".legacylens.json")


def find_config_path(start: Path | None = None) -> Path | None:
    explicit = os.environ.get("LEGACYLENS_CONFIG")
    if explicit:
        return Path(explicit).expanduser().resolve()

    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for filename in CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.exists():
                return candidate
    return None


def load_config_payload(start: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    path = find_config_path(start)
    if path is None:
        return {}, None
    if not path.exists():
        raise ValueError(f"Legacy Lens config file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Legacy Lens config file is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Legacy Lens config file must contain a JSON object: {path}")
    return payload, path


def load_config_payload_or_empty() -> tuple[dict[str, Any], Path | None]:
    try:
        return load_config_payload()
    except ValueError:
        return {}, None


def logging_level(default: str = "INFO") -> str:
    payload, _ = load_config_payload_or_empty()
    logging_config = mapping(payload.get("logging")) or mapping(payload.get("log"))
    return (
        first_string(
            os.environ.get("LEGACYLENS_LOG_LEVEL"),
            logging_config.get("level"),
            payload.get("logLevel"),
            payload.get("log_level"),
            default,
        )
        or default
    ).upper()


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
