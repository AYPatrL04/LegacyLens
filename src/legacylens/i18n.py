from __future__ import annotations

import locale
import os
import re
from dataclasses import dataclass

from .config import first_string, load_config_payload_or_empty, mapping


@dataclass(frozen=True)
class OutputLanguage:
    code: str
    prompt_name: str
    section_names: tuple[str, str, str, str]
    near_code_phrase: str
    deterministic_language: str


ENGLISH = OutputLanguage(
    code="en",
    prompt_name="English",
    section_names=("Behavior", "Role In Current Context", "Impact", "Next Checks"),
    near_code_phrase="near this code",
    deterministic_language="en",
)

SIMPLIFIED_CHINESE = OutputLanguage(
    code="zh-Hans",
    prompt_name="Simplified Chinese",
    section_names=("行为", "在当前目录/项目中的作用", "影响面", "下一步检查"),
    near_code_phrase="在这段代码附近",
    deterministic_language="zh-Hans",
)

TRADITIONAL_CHINESE = OutputLanguage(
    code="zh-Hant",
    prompt_name="Traditional Chinese",
    section_names=("行為", "在目前目錄/專案中的作用", "影響面", "下一步檢查"),
    near_code_phrase="在這段程式碼附近",
    deterministic_language="en",
)

PROMPT_LANGUAGES: dict[str, OutputLanguage] = {
    "en": ENGLISH,
    "zh-Hans": SIMPLIFIED_CHINESE,
    "zh-Hant": TRADITIONAL_CHINESE,
    "ja": OutputLanguage(
        code="ja",
        prompt_name="Japanese",
        section_names=("動作", "現在のディレクトリ/プロジェクトでの役割", "影響範囲", "次に確認すること"),
        near_code_phrase="このコード付近",
        deterministic_language="en",
    ),
    "ko": OutputLanguage(
        code="ko",
        prompt_name="Korean",
        section_names=("동작", "현재 디렉터리/프로젝트에서의 역할", "영향 범위", "다음 확인 사항"),
        near_code_phrase="이 코드 근처",
        deterministic_language="en",
    ),
    "fr": OutputLanguage(
        code="fr",
        prompt_name="French",
        section_names=("Comportement", "Role dans le contexte actuel", "Impact", "Prochaines verifications"),
        near_code_phrase="pres de ce code",
        deterministic_language="en",
    ),
    "de": OutputLanguage(
        code="de",
        prompt_name="German",
        section_names=("Verhalten", "Rolle im aktuellen Kontext", "Auswirkungen", "Naechste Pruefungen"),
        near_code_phrase="in der Naehe dieses Codes",
        deterministic_language="en",
    ),
    "es": OutputLanguage(
        code="es",
        prompt_name="Spanish",
        section_names=("Comportamiento", "Rol en el contexto actual", "Impacto", "Siguientes revisiones"),
        near_code_phrase="cerca de este codigo",
        deterministic_language="en",
    ),
    "pt": OutputLanguage(
        code="pt",
        prompt_name="Portuguese",
        section_names=("Comportamento", "Papel no contexto atual", "Impacto", "Proximas verificacoes"),
        near_code_phrase="perto deste codigo",
        deterministic_language="en",
    ),
    "ru": OutputLanguage(
        code="ru",
        prompt_name="Russian",
        section_names=("Behavior", "Role In Current Context", "Impact", "Next Checks"),
        near_code_phrase="near this code",
        deterministic_language="en",
    ),
    "it": OutputLanguage(
        code="it",
        prompt_name="Italian",
        section_names=("Comportamento", "Ruolo nel contesto attuale", "Impatto", "Controlli successivi"),
        near_code_phrase="vicino a questo codice",
        deterministic_language="en",
    ),
}

AUTO_LANGUAGE_VALUES = {"", "auto", "system", "editor", "default", "locale"}


def resolve_output_language(value: str | None = None, ui_language: str | None = None) -> OutputLanguage:
    candidate = _requested_or_system_locale(value, ui_language)
    normalized = _normalize_language_code(candidate)
    return PROMPT_LANGUAGES.get(normalized, ENGLISH)


def _requested_or_system_locale(value: str | None, ui_language: str | None) -> str:
    payload, _ = load_config_payload_or_empty()
    i18n = mapping(payload.get("i18n"))
    analysis = mapping(payload.get("analysis"))
    configured = (
        first_string(
            payload.get("outputLanguage"),
            payload.get("output_language"),
            i18n.get("outputLanguage"),
            i18n.get("output_language"),
            analysis.get("outputLanguage"),
            analysis.get("output_language"),
        )
        or ""
    ).strip()
    if configured and configured.lower() not in AUTO_LANGUAGE_VALUES:
        return configured

    requested = (value or "").strip()
    if requested and requested.lower() not in AUTO_LANGUAGE_VALUES:
        return requested

    editor_locale = (ui_language or "").strip()
    if editor_locale and editor_locale.lower() not in AUTO_LANGUAGE_VALUES:
        return editor_locale

    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        candidate = os.environ.get(name, "").strip()
        if candidate:
            return candidate.split(":", 1)[0]

    system_locale = locale.getlocale()[0]
    return system_locale or "en"


def _normalize_language_code(value: str) -> str:
    text = value.strip()
    if not text:
        return "en"
    lowered = text.lower().replace("_", "-")
    lowered = lowered.split(".", 1)[0]
    lowered = lowered.split("@", 1)[0]
    lowered = re.sub(r"[^a-z0-9-]", "", lowered)

    if lowered in {"c", "posix"}:
        return "en"
    if lowered in {"english", "en", "en-us", "en-gb", "en-au", "en-ca"} or lowered.startswith(("en-", "english-")):
        return "en"
    if lowered in {"chinese", "simplified-chinese", "zh", "zh-cn", "zh-sg", "zh-hans"} or lowered.startswith("zh-hans"):
        return "zh-Hans"
    if lowered in {"traditional-chinese", "zh-tw", "zh-hk", "zh-mo", "zh-hant"} or lowered.startswith("zh-hant"):
        return "zh-Hant"
    if lowered.startswith("chinese"):
        return "zh-Hans"

    named_languages = {
        "japanese": "ja",
        "korean": "ko",
        "french": "fr",
        "german": "de",
        "spanish": "es",
        "portuguese": "pt",
        "russian": "ru",
        "italian": "it",
    }
    for prefix, code in named_languages.items():
        if lowered == prefix or lowered.startswith(f"{prefix}-"):
            return code

    primary = lowered.split("-", 1)[0]
    if primary in PROMPT_LANGUAGES:
        return primary
    return "en"
