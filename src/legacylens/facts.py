from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

from .models import Fact, Finding

LANGUAGE_TAGS = {"asm", "c", "cobol", "cpp", "fortran"}


DEFAULT_FACTS = (
    Fact(
        fact_id="bit-packing-memory-pressure",
        title="Bit packing under memory pressure",
        summary=(
            "Packed flags reduced memory and I/O size when machines commonly had kilobytes of RAM "
            "and storage was expensive."
        ),
        tags=("bit-packing", "flags", "packed-data"),
        source="project seed data",
    ),
    Fact(
        fact_id="common-blocks-global-layout",
        title="COMMON blocks as global binary layout",
        summary=(
            "Fortran COMMON blocks acted as shared named storage, so field order became part of the "
            "program's binary contract."
        ),
        tags=("common", "memory-overlay", "fortran"),
        source="project seed data",
    ),
)


class FactStore:
    def __init__(self, facts: list[Fact] | None = None) -> None:
        self._facts = facts if facts is not None else self._load_default_facts()

    @classmethod
    def from_path(cls, path: str | Path) -> "FactStore":
        return cls(_load_jsonl(Path(path)))

    def retrieve(self, findings: list[Finding], query: str = "", limit: int = 3) -> list[Fact]:
        if not self._facts:
            return []

        finding_tags = {tag for finding in findings for tag in finding.tags}
        finding_languages = {finding.language for finding in findings}
        if "cpp" in finding_languages:
            finding_languages.add("c")
        query_terms = set(_terms(query))
        scored: list[tuple[int, Fact]] = []
        for fact in self._facts:
            fact_languages = LANGUAGE_TAGS.intersection(fact.tags)
            if fact_languages and finding_languages and not fact_languages.intersection(finding_languages):
                continue
            tag_overlap = len(finding_tags.intersection(fact.tags))
            if finding_tags and tag_overlap == 0:
                continue
            score = 0
            score += 4 * tag_overlap
            text = f"{fact.title} {fact.summary}".lower()
            score += sum(1 for term in query_terms if term in text)
            if score:
                scored.append((score, fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [fact for _, fact in scored[:limit]]

    def _load_default_facts(self) -> list[Fact]:
        try:
            data_path = resources.files("legacylens").joinpath("data/legacy_facts.jsonl")
            return _load_jsonl(data_path)
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            return list(DEFAULT_FACTS)


def _load_jsonl(path: str | Path) -> list[Fact]:
    facts: list[Fact] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            facts.append(
                Fact(
                    fact_id=str(payload["fact_id"]),
                    title=str(payload["title"]),
                    summary=str(payload["summary"]),
                    tags=tuple(payload.get("tags", [])),
                    source=payload.get("source"),
                )
            )
    return facts or list(DEFAULT_FACTS)


def _terms(text: str) -> list[str]:
    return [term for term in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if len(term) > 2]
