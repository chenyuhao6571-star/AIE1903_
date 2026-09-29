from __future__ import annotations

import re

from .config import rubric, subject_mapping
from .subject_mapping import _clusters_for, _expand


def _tokens(text: str | None) -> set[str]:
    return _expand(text or "")


def _target_tokens(required_fields: list[str], research_areas: list[str], skills: list[str] | None = None) -> set[str]:
    return _tokens(" ".join(required_fields + research_areas + (skills or [])))


def classify_relevance(
    source_text: str | None,
    required_fields: list[str],
    research_areas: list[str],
    skills: list[str] | None = None,
) -> tuple[str, float]:
    """Return a rubric relevance bucket and an evidence-quality confidence.

    This is a transparent, deterministic approximation for the MVP. The LLM is
    still responsible for extracting facts; semantic relevance here is based on
    explicit keyword/subject overlap and can later be replaced by an LLM-backed
    classifier without changing the scoring contract.
    """
    source = source_text or ""
    if not source.strip():
        return "unknown", 0.0

    source_tokens = _tokens(source)
    target_tokens = _target_tokens(required_fields, research_areas, skills)
    overlap = source_tokens & target_tokens
    source_clusters = _clusters_for(source)
    target_clusters = _clusters_for(" ".join(required_fields + research_areas))

    if source_clusters and source_clusters & target_clusters:
        bucket = "direct_match"
    elif overlap:
        ratio = len(overlap) / max(len(source_tokens), 1)
        if ratio >= 0.4:
            bucket = "very_closely_related"
        else:
            bucket = "related"
    elif source_clusters and target_clusters:
        bucket = "some_relevance"
    elif any(term in source for term in ("history", "literature", "art", "law", "philosophy", "sociology", "anthropology")):
        bucket = "unrelated"
    else:
        bucket = "some_relevance"

    buckets = rubric()["relevance_buckets"]
    confidence = 0.95 if bucket == "direct_match" else 0.8 if bucket in ("very_closely_related", "related") else 0.65 if bucket == "some_relevance" else 0.5
    return bucket, confidence


def relevance_score(bucket: str) -> float | None:
    buckets = rubric()["relevance_buckets"]
    item = buckets.get(bucket)
    if not item:
        return None
    return item.get("default")


def relevance_label(bucket: str) -> str:
    return rubric()["relevance_buckets"].get(bucket, {}).get("label", bucket)
