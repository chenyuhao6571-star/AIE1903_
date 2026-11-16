from __future__ import annotations

import re

from .config import subject_mapping
from .models import JobRequirements


_DEGREE_PATTERNS = [
    (r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b|博士", "PhD"),
    (r"\bmaster'?s\b|\bm\.?s\.?\b|\bm\.?sc\b|\bm\.?a\.?\b|\bm\.?phil\b|硕士|研究生", "Master's"),
    (r"\bbachelor'?s\b|\bb\.?s\.?\b|\bb\.?sc\b|\bb\.?a\.?\b|本科|学士", "Bachelor's"),
]

_REQUIRED_MARKERS = (
    "must hold", "must have", "is required", "are required", "required", "requirement",
    "必需", "必须", "须", "应具有", "应具备", "应取得", "要求",
)
_PREFERRED_MARKERS = (
    "preferred", "prefer", "desirable", "a plus", "is an advantage", "nice to have",
    "优先", "最好", "更佳", "加分",
)


def _degree_requirement(text: str) -> tuple[str | None, str | None, str, str | None]:
    """Classify degree requirements as required vs. preferred.

    Returns (minimum_degree, preferred_degree, requirement_kind, evidence_text).
    This avoids the previous bug where "Bachelor's required, PhD preferred"
    was read as a PhD minimum.
    """
    lowered = text.lower()
    mentions: list[tuple[int, int, str]] = []
    for pattern, label in _DEGREE_PATTERNS:
        for match in re.finditer(pattern, lowered):
            mentions.append((match.start(), match.end(), label))
    mentions.sort()

    required: list[str] = []
    preferred: list[str] = []
    def next_break(position: int) -> int:
        for char in (",", ";", "。", "，", "；"):
            index = lowered.find(char, position)
            if index != -1:
                return index
        return len(lowered)

    for start, end, label in mentions:
        right = min(len(lowered), next_break(end))
        context = lowered[end:right]
        is_preferred = any(marker in context for marker in _PREFERRED_MARKERS)
        is_required = any(marker in context for marker in _REQUIRED_MARKERS)
        if is_preferred and not is_required:
            preferred.append(label)
        else:
            required.append(label)

    # De-duplicate while preserving the most common sense ordering.
    def unique(items: list[str]) -> list[str]:
        seen: list[str] = []
        for item in items:
            if item not in seen:
                seen.append(item)
        return seen

    required = unique(required)
    preferred = unique(preferred)

    # If only "preferred" was stated (e.g. "PhD preferred"), treat the stated
    # degree as the minimum benchmark but record it as preferred-only.
    if not required and preferred:
        minimum_degree = preferred[0]
        preferred_degree = None
        kind = "preferred_only"
    else:
        minimum_degree = required[0] if required else None
        preferred_degree = preferred[0] if preferred else None
        kind = "required_and_preferred" if preferred_degree else ("required" if minimum_degree else "not_stated")

    evidence = None
    for match in re.finditer(r"[^.\n;]*[^\x00]{0,120}", text, re.IGNORECASE):
        sentence = match.group(0)
        if any(marker in sentence.lower() for marker in _REQUIRED_MARKERS + _PREFERRED_MARKERS):
            evidence = sentence.strip()
            break
    return minimum_degree, preferred_degree, kind, evidence


def _title(lines: list[str], text: str) -> str:
    for line in lines:
        clean = line.strip()
        if re.search(r"(professor|lecturer|faculty|postdoc|post-doctoral|research (fellow|scientist))", clean, re.I):
            return clean[:180]
    first = (lines[0].strip() if lines else text.strip())
    return (first[:180] if first else "Faculty Position")


def _field_mentions(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    aliases = {
        "Computer Science": [r"\bcomputer science\b", r"\bcomputing\b", r"\bsoftware engineering\b", r"\binformatics\b"],
        "Artificial Intelligence": [r"\bartificial intelligence\b", r"\bai\b"],
        "Statistics": [r"\bstatistics\b", r"\bbiostatistics\b"],
        "Mathematics": [r"\bmathematics\b", r"\bapplied mathematics\b"],
        "Engineering": [r"\bengineering\b", r"\bmechanical engineering\b", r"\belectrical engineering\b", r"\bcivil engineering\b", r"\bchemical engineering\b"],
        "Business & Economics": [r"\bbusiness\b", r"\beconomics\b", r"\bfinance\b", r"\baccounting\b", r"\bmarketing\b"],
    }
    for field, patterns in aliases.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            found.append(field)
    return found


def _research_areas(text: str) -> list[str]:
    lowered = text.lower()
    data = subject_mapping()
    synonyms = data.get("synonyms", {})
    canonical = {
        "llm": "Large Language Models",
        "llms": "Large Language Models",
        "large language model": "Large Language Models",
        "large language models": "Large Language Models",
        "ai agents": "AI Agents",
        "machine learning": "Machine Learning",
        "deep learning": "Deep Learning",
        "natural language processing": "Natural Language Processing",
        "computer vision": "Computer Vision",
        "robotics": "Robotics",
        "data science": "Data Science",
    }
    areas: list[str] = []
    for key, label in canonical.items():
        pattern = rf"\b{re.escape(key)}\b"
        if re.search(pattern, lowered) and label not in areas:
            areas.append(label)
    # Include a few synonym expansions that are meaningful research areas.
    for key in ("ml", "nlp", "dl", "hci"):
        if re.search(rf"\b{re.escape(key)}\b", lowered):
            label = {
                "ml": "Machine Learning",
                "nlp": "Natural Language Processing",
                "dl": "Deep Learning",
                "hci": "Human-Computer Interaction",
            }[key]
            if label not in areas:
                areas.append(label)
    return areas


def extract_job_requirements(text: str, title: str | None = None, source_pages: list[int] | None = None) -> JobRequirements:
    """Extract a lightweight, deterministic JobRequirements profile from free text.

    The MVP keeps this heuristic and fully auditable; an LLM-backed parser can
    replace it later without changing the deterministic scoring contract.
    """
    clean = (text or "").strip()
    lines = [line for line in clean.splitlines() if line.strip()]
    minimum_degree, preferred_degree, degree_requirement_kind, degree_evidence = _degree_requirement(clean)
    return JobRequirements(
        title=title or _title(lines, clean),
        minimum_degree=minimum_degree,
        preferred_degree=preferred_degree,
        degree_requirement_kind=degree_requirement_kind,
        degree_evidence_text=degree_evidence,
        required_fields=_field_mentions(clean),
        research_areas=_research_areas(clean),
        required_skills=[],
        qualification_text=clean,
        source_pages=source_pages or [],
    )
