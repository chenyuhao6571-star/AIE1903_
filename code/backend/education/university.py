from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from .config import university_aliases


class UniversityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    canonical: str
    matched_alias: str | None = None
    ambiguous: bool = False
    status: str = "resolved"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def resolve_university(name: str) -> UniversityResolution:
    raw = (name or "").strip()
    normalized = _normalize(raw)
    data = university_aliases()
    aliases: dict[str, str] = {_normalize(k): v for k, v in data["aliases"].items()}
    ambiguous = {_normalize(v) for v in data["ambiguous"]}

    if not normalized:
        return UniversityResolution(input=raw, canonical=raw, status="missing")

    if normalized in aliases:
        canonical = aliases[normalized]
        return UniversityResolution(
            input=raw,
            canonical=canonical,
            matched_alias=raw,
            ambiguous=normalized in ambiguous,
            status="ambiguous" if normalized in ambiguous else "resolved",
        )

    for alias, canonical in aliases.items():
        if alias and (alias in normalized or normalized in alias):
            return UniversityResolution(
                input=raw,
                canonical=canonical,
                matched_alias=alias,
                ambiguous=normalized in ambiguous,
                status="ambiguous" if normalized in ambiguous else "resolved",
            )

    return UniversityResolution(
        input=raw,
        canonical=raw,
        ambiguous=normalized in ambiguous,
        status="ambiguous" if normalized in ambiguous else "resolved",
    )
