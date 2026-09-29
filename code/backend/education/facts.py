from __future__ import annotations

import re
from datetime import datetime

from backend.schema import Education, Profile

from .models import AdditionalEducation, Candidate, Degree, EducationFacts, EducationGap, Evidence, Honor


DEGREE_ORDER = {"PhD": 4, "Doctorate": 4, "Master's": 3, "Bachelor's": 2, "Below Bachelor's": 1, "Not stated": 0}


def _extract_years(period: str | None) -> tuple[int | None, int | None]:
    if not period:
        return None, None
    numbers = [int(n) for n in re.findall(r"\b(?:19|20)\d{2}\b", period)]
    numbers = [n for n in numbers if 1900 <= n <= 2100]
    if not numbers:
        return None, None
    return min(numbers), max(numbers)


def _infer_field(education: Education) -> str | None:
    text = " ".join(filter(None, [education.department, education.thesis]))
    text = text.lower()
    if "computer" in text or "software" in text or "information" in text:
        return "Computer Science"
    if "mechanical" in text or "electrical" in text or "engineering" in text:
        return "Engineering"
    if "mathematics" in text or "statistics" in text:
        return "Mathematics"
    if "business" in text or "finance" in text or "economics" in text:
        return "Business & Economics"
    return None


def _degree_kind(degree: str) -> tuple[str, str]:
    text = degree.lower()
    if "ph.d" in text or "phd" in text or "doctor" in text:
        return "doctorate", "PhD"
    if "master" in text or "m.s" in text or "msc" in text or "m.a" in text or "m.phil" in text:
        return "master", "Master's"
    if "bachelor" in text or "b.s" in text or "bsc" in text or "b.a" in text:
        return "bachelor", "Bachelor's"
    return "below_bachelor", degree.strip() or "Not stated"


def _degree_level(kind: str) -> int:
    return {"doctorate": 4, "master": 3, "bachelor": 2, "below_bachelor": 1}.get(kind, 0)


def build_education_facts(profile: Profile, candidate_id: str) -> EducationFacts:
    degrees: list[Degree] = []
    for i, education in enumerate(profile.education):
        start, end = _extract_years(education.period)
        kind, label = _degree_kind(education.degree)
        if "present" in (education.period or "").lower() or "now" in (education.period or "").lower():
            status = "in_progress"
        elif end and end <= datetime.now().year:
            status = "completed"
        else:
            status = "not_stated"
        field = education.field if hasattr(education, "field") and education.field else _infer_field(education)
        degrees.append(
            Degree(
                degree_type=label,
                field=field,
                specialization=getattr(education, "specialization", None) or None,
                institution=education.institution,
                start_year=start,
                graduation_year=end,
                thesis_title=education.thesis,
                status=status,
                source_pages=education.source_pages,
                evidence=[
                    Evidence(
                        evidence_id=f"EDU-{i + 1:03d}",
                        claim=f"{label} in {field or 'an unspecified field'} from {education.institution}",
                        source_type="CV",
                        source_location=", ".join(str(p) for p in education.source_pages),
                        source_text=None,
                        confidence=0.9 if education.source_pages else 0.5,
                        verified=False,
                    )
                ],
            )
        )

    degrees.sort(
        key=lambda d: (
            DEGREE_ORDER.get(d.degree_type, 0),
            d.graduation_year or d.start_year or 0,
        ),
        reverse=True,
    )

    honors: list[Honor] = []
    for i, award in enumerate(profile.achievements_awards):
        honors.append(
            Honor(
                title=award.title,
                year=award.year,
                organization=award.organization,
                significance=_honor_significance(award.title, award.description),
                source_pages=award.source_pages,
                evidence=[
                    Evidence(
                        evidence_id=f"HON-{i + 1:03d}",
                        claim=award.title,
                        source_type="CV",
                        source_location=", ".join(str(p) for p in award.source_pages),
                        confidence=0.8,
                        verified=False,
                    )
                ],
            )
        )

    additional: list[AdditionalEducation] = []
    highest_kind = degrees[0].degree_type if degrees else ""
    for degree in degrees:
        if degree.degree_type != highest_kind:
            additional.append(
                AdditionalEducation(
                    kind=degree.degree_type,
                    field=degree.field,
                    institution=degree.institution,
                    year=degree.graduation_year or degree.start_year,
                    relevance="relevant" if degree.field else "unknown",
                    source_pages=degree.source_pages,
                )
            )

    gaps = _find_gaps(degrees)
    return EducationFacts(
        candidate=Candidate(
            candidate_id=candidate_id,
            name=profile.bio.name,
            current_title=profile.bio.current_position,
            current_institution=profile.bio.institution,
        ),
        degrees=degrees,
        honors=honors,
        additional_education=additional,
        education_gaps=gaps,
        unverified_claims=[],
        dissertation_status=_dissertation_status(degrees),
    )


def _honor_significance(title: str, description: str | None) -> str:
    text = (title + " " + (description or "")).lower()
    if any(k in text for k in ["outstanding", "distinguished", "national scholarship", "medal", "summa", "magna"]):
        return "exceptional"
    if any(k in text for k in ["scholarship", "fellowship", "dean", "award", "prize", "first-class"]):
        return "significant"
    return "minor"


def _dissertation_status(degrees: list[Degree]) -> str:
    for degree in degrees:
        if degree.degree_type == "PhD" and degree.thesis_title:
            return "found"
    if any(d.degree_type == "PhD" for d in degrees):
        return "not_found"
    return "not_applicable"


def _find_gaps(degrees: list[Degree]) -> list[EducationGap]:
    ordered = sorted(degrees, key=lambda d: (d.start_year or d.graduation_year or 0, d.graduation_year or 0))
    gaps: list[EducationGap] = []
    for earlier, later in zip(ordered, ordered[1:]):
        earlier_end = earlier.graduation_year or earlier.start_year
        later_start = later.start_year or later.graduation_year
        if earlier_end is None or later_start is None:
            continue
        duration = later_start - earlier_end
        if duration >= 3:
            gaps.append(
                EducationGap(
                    start_year=earlier_end,
                    end_year=later_start,
                    duration_years=duration,
                    note=f"Education gap identified: approximately {duration} years. Reason not stated.",
                    inferred=False,
                )
            )
    return gaps
