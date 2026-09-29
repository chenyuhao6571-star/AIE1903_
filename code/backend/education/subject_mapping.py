from __future__ import annotations

import re

from .config import subject_mapping


def _expand(text: str) -> set[str]:
    data = subject_mapping()
    tokens: set[str] = set()
    lowered = text.lower()
    for short, full in data.get("synonyms", {}).items():
        pattern = rf"\b{re.escape(short)}\b"
        if re.search(pattern, lowered):
            tokens.add(full)
            tokens.add(short)
    for token in re.findall(r"[a-z][a-z0-9-]{2,}", lowered):
        tokens.add(token)
    return tokens


def _clusters_for(text: str) -> set[str]:
    data = subject_mapping()
    tokens = _expand(text)
    found: set[str] = set()
    for cluster in data.get("clusters", []):
        if any(kw in text.lower() or kw in tokens or any(t in kw for t in tokens) for kw in cluster["keywords"]):
            found.add(cluster["name"])
    return found


def primary_subject(field: str | None, specialization: str | None = None) -> str | None:
    data = subject_mapping()
    clusters = _clusters_for((field or "") + " " + (specialization or ""))
    for cluster_name in clusters:
        mapped = data.get("primary_subject_for_cluster", {}).get(cluster_name)
        if mapped:
            return mapped
    return None
