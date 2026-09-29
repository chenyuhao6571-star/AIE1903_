from __future__ import annotations

import re

from .config import subject_mapping
from .models import JobRequirements


_DEGREE_PATTERNS = [
    (r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b", "PhD"),
    (r"\bmaster'?s\b|\bm\.?s\.?\b|\bm\.?sc\b|\bm\.?a\.?\b|\bm\.?phil\b", "Master's"),
    (r"\bbachelor'?s\b|\bb\.?s\.?\b|\bb\.?sc\b|\bb\.?a\.?\b", "Bachelor's"),
]


def _minimum_degree(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b", lowered):
        return "PhD"
    if re.search(r"\bmaster'?s\b|\bm\.?s\.?\b|\bm\.?sc\b", lowered):
        return "Master's"
    if re.search(r"\bbachelor'?s\b|\bb\.?s\.?\b|\bb\.?sc\b|\bb\.?a\.?\b", lowered):
        return "Bachelor's"
    return None


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
    minimum_degree = _minimum_degree(clean)
    return JobRequirements(
        title=title or _title(lines, clean),
        minimum_degree=minimum_degree,
        required_fields=_field_mentions(clean),
        research_areas=_research_areas(clean),
        required_skills=[],
        qualification_text=clean,
        source_pages=source_pages or [],
    )
