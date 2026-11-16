from __future__ import annotations

from datetime import datetime

from .config import rubric
from .models import (
    AdditionalEducation,
    CalculationLine,
    ComponentScore,
    Degree,
    EducationAssessment,
    EducationFacts,
    EducationScore,
    Evidence,
    JobRequirements,
    QualificationCheck,
    RankingRecord,
    ReviewAction,
)
from .ranking import RankingRepository
from .relevance import classify_relevance, relevance_label, relevance_score
from .subject_mapping import primary_subject
from .university import resolve_university


DEGREE_LEVELS = {"PhD": 4, "Doctorate": 4, "Master's": 3, "Bachelor's": 2, "Below Bachelor's": 1}


def ranking_period_label(ranking_year: int | None, graduation_year: int | None) -> str:
    """Label a ranking relative to the candidate's degree year.

    A ranking near the degree year is historical evidence; otherwise it is
    treated as current institutional strength and must never be presented as
    the institution's position at graduation.
    """
    if ranking_year is None:
        return "Current ranking"
    if graduation_year is not None and abs(ranking_year - graduation_year) <= 2:
        return f"Historical ranking near degree year ({ranking_year})"
    return f"Current ranking ({ranking_year})"


def _evidence(evidence_id: str, claim: str, source_type: str, source_location: str | None = None, confidence: float | None = None, verified: bool = False, source_url: str | None = None, ranking_year: int | None = None, verification_status: str = "unverified") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim=claim,
        source_type=source_type,
        source_location=source_location,
        confidence=confidence,
        verified=verified,
        source_url=source_url,
        ranking_year=ranking_year,
        verification_status=verification_status,
    )


def _primary_degree(facts: EducationFacts) -> Degree | None:
    ranked = sorted(facts.degrees, key=lambda d: (DEGREE_LEVELS.get(d.degree_type, 0), d.graduation_year or d.start_year or 0), reverse=True)
    return ranked[0] if ranked else None


def _highest_completed_degree(facts: EducationFacts) -> Degree | None:
    """Highest degree that is actually awarded, ignoring in-progress entries."""
    completed = [d for d in facts.degrees if d.status == "completed"]
    ranked = sorted(completed, key=lambda d: (DEGREE_LEVELS.get(d.degree_type, 0), d.graduation_year or d.start_year or 0), reverse=True)
    return ranked[0] if ranked else None


def _highest_in_progress_degree(facts: EducationFacts) -> Degree | None:
    in_progress = [d for d in facts.degrees if d.status == "in_progress"]
    ranked = sorted(in_progress, key=lambda d: (DEGREE_LEVELS.get(d.degree_type, 0), d.start_year or 0), reverse=True)
    return ranked[0] if ranked else None


def _degree_level_name(degree: Degree | None) -> str:
    if degree is None:
        return "none"
    if degree.degree_type in ("PhD", "Doctorate"):
        return "doctorate"
    if degree.degree_type == "Master's":
        return "master"
    if degree.degree_type == "Bachelor's":
        return "bachelor"
    return "below_bachelor"


class EducationScorer:
    def __init__(self, ranking_repository: RankingRepository | None = None) -> None:
        self.rankings = ranking_repository or RankingRepository()
        self.rubric = rubric()

    def assess(self, facts: EducationFacts, job: JobRequirements, job_id: str | None = None) -> EducationAssessment:
        degree = _primary_degree(facts)
        field_text = " ".join(filter(None, [degree.field if degree else None, degree.specialization if degree else None]))

        field_bucket, field_confidence = classify_relevance(
            field_text or None, job.required_fields, job.research_areas, job.required_skills
        )

        qualification = self._qualification(facts, job, degree, field_bucket)
        degree_level = self._degree_level(degree, field_bucket, facts)
        field_relevance = self._field_relevance(field_bucket, field_confidence, degree, job)
        overall = self._overall_university(degree)
        subject = self._subject_university(degree, field_text)
        dissertation = self._dissertation(degree, job)
        progression = self._progression(facts)
        honors = self._honors(facts)
        additional = self._additional_education(facts, job)

        components = {
            "degree_level": degree_level,
            "field_relevance": field_relevance,
            "overall_university_strength": overall,
            "subject_university_strength": subject,
            "dissertation_relevance": dissertation,
            "academic_progression": progression,
            "academic_honors": honors,
            "additional_relevant_education": additional,
        }

        score, calculation = self._combine(components)
        missing = self._missing_information(components, facts)
        evidence_quality = self._evidence_quality(components)
        external_incomplete = any(
            c.score is None and c.name in {"overall_university_strength", "subject_university_strength"}
            for c in components.values()
        )

        return EducationAssessment(
            candidate_id=facts.candidate.candidate_id,
            job_id=job_id or facts.candidate.candidate_id,
            candidate_name=facts.candidate.name,
            position_title=job.title,
            qualification_check=qualification,
            education_score=EducationScore(
                final_score=round(score, 2) if score is not None else None,
                components=components,
                calculation=calculation,
                external_evidence_incomplete=external_incomplete,
            ),
            evidence_quality=evidence_quality,
            missing_information=missing,
            review_status="PENDING",
            rubric_version=self.rubric["rubric_version"],
        )

    def _qualification(self, facts: EducationFacts, job: JobRequirements, degree: Degree | None, field_bucket: str) -> QualificationCheck:
        minimum_degree = (job.minimum_degree or "").strip().lower()
        completed = _highest_completed_degree(facts)
        in_progress = _highest_in_progress_degree(facts)
        completed_level = DEGREE_LEVELS.get(completed.degree_type if completed else "", 0)
        required_level = 0
        if any(k in minimum_degree for k in ("phd", "doctor")):
            required_level = 4
        elif "master" in minimum_degree:
            required_level = 3
        elif "bachelor" in minimum_degree:
            required_level = 2

        minimum_met: bool | None = None if not minimum_degree else completed_level >= required_level
        field_met: bool | None = None
        if field_bucket == "unknown":
            field_met = None
        elif job.required_fields:
            field_met = field_bucket in ("direct_match", "very_closely_related", "related")

        if minimum_met is False or field_met is False:
            status = "NOT MET"
        elif minimum_met is None and field_met is None:
            status = "UNKNOWN"
        elif minimum_met is True and (field_met is None or field_met is True):
            status = "MET"
        else:
            status = "PARTIAL"

        parts = []
        if completed:
            parts.append(
                f"Highest completed degree: {completed.degree_type}"
                + (f" in {completed.field}" if completed.field else "")
            )
        else:
            parts.append("No completed degree is documented.")
        if in_progress:
            parts.append(
                f"In progress: {in_progress.degree_type}"
                + (f" in {in_progress.field}" if in_progress.field else "")
                + ". This is not counted as an awarded degree."
            )
        if minimum_degree:
            benchmark = job.minimum_degree
            if job.degree_requirement_kind == "preferred_only":
                benchmark += " (stated as preferred, not required)"
            parts.append(
                f"Job minimum degree: {job.minimum_degree}. "
                f"Candidate's awarded degree level {'meets' if minimum_met else 'does not meet'} this requirement."
            )
            if job.preferred_degree:
                parts.append(f"Preferred degree: {job.preferred_degree}.")
        else:
            parts.append("Job description did not state a minimum degree.")
        if job.required_fields:
            parts.append(f"Field relevance: {relevance_label(field_bucket)}.")
        explanation = " ".join(parts)
        evidence = []
        if completed:
            evidence.append(
                _evidence(
                    "QUAL-001",
                    f"{completed.degree_type} from {completed.institution}",
                    "CV",
                    source_location=", ".join(str(p) for p in completed.source_pages),
                    confidence=0.9,
                )
            )
        if in_progress:
            evidence.append(
                _evidence(
                    "QUAL-002",
                    f"In-progress {in_progress.degree_type} from {in_progress.institution} (not yet awarded)",
                    "CV",
                    source_location=", ".join(str(p) for p in in_progress.source_pages),
                    confidence=0.9,
                )
            )
        return QualificationCheck(
            minimum_degree_met=minimum_met,
            field_requirement_met=field_met,
            status=status,
            explanation=explanation,
            evidence=evidence,
            in_progress_degree=in_progress.degree_type if in_progress else None,
            preferred_degree=job.preferred_degree,
        )

    def _degree_level(self, degree: Degree | None, field_bucket: str, facts: EducationFacts) -> ComponentScore:
        config = self.rubric["degree_levels"][_degree_level_name(degree)]
        if degree is None:
            score = 0
            explanation = "No verifiable degree information was documented."
            confidence = 0.5
        else:
            relevant = field_bucket in ("direct_match", "very_closely_related", "related")
            score = float(config["relevant_score"] if relevant else config["partial_relevant_score"])
            status_note = " (in progress, not yet awarded)" if degree.status == "in_progress" else ""
            explanation = (
                f"Highest documented degree is {degree.degree_type}{status_note}"
                + (f" in {degree.field}" if degree.field else "")
                + f". Field relevance is {relevance_label(field_bucket)}."
            )
            confidence = 0.95 if degree.source_pages else 0.65
        return ComponentScore(
            name="degree_level",
            label=self.rubric["components"]["degree_level"]["label"],
            score=score,
            weight=self.rubric["components"]["degree_level"]["weight"],
            confidence=confidence,
            explanation=explanation,
            evidence=[_evidence("DEG-001", explanation, "CV", confidence=confidence)] if degree else [],
        )

    def _field_relevance(self, bucket: str, confidence: float, degree: Degree | None, job: JobRequirements) -> ComponentScore:
        score = relevance_score(bucket)
        explanation = (
            f"Degree field relevance to the job is {relevance_label(bucket)}."
            + (f" Job fields: {', '.join(job.required_fields)}." if job.required_fields else "")
        )
        evidence = []
        if job.source_pages:
            evidence.append(
                _evidence("FR-001", "Job field requirements", "Job description", source_location=", ".join(str(p) for p in job.source_pages), confidence=0.95)
            )
        if degree:
            evidence.append(
                _evidence("FR-002", f"{degree.degree_type} field: {degree.field or 'not stated'}", "CV", source_location=", ".join(str(p) for p in degree.source_pages), confidence=confidence)
            )
        return ComponentScore(
            name="field_relevance",
            label=self.rubric["components"]["field_relevance"]["label"],
            score=score,
            weight=self.rubric["components"]["field_relevance"]["weight"],
            confidence=confidence,
            explanation=explanation,
            evidence=evidence,
        )

    def _overall_university(self, degree: Degree | None) -> ComponentScore:
        if degree is None:
            return self._unavailable_component("overall_university_strength", "No degree institution is available.")
        score, records = self.rankings.combined_standardized_score(degree.institution, "overall")
        if score is None:
            return ComponentScore(
                name="overall_university_strength",
                label=self.rubric["components"]["overall_university_strength"]["label"],
                score=None,
                weight=self.rubric["components"]["overall_university_strength"]["weight"],
                confidence=0.0,
                status="not_found",
                explanation=f"No overall ranking found for {resolve_university(degree.institution).canonical}. Absence from a ranking is not treated as zero.",
                evidence=[],
            )
        return ComponentScore(
            name="overall_university_strength",
            label=self.rubric["components"]["overall_university_strength"]["label"],
            score=round(score, 2),
            weight=self.rubric["components"]["overall_university_strength"]["weight"],
            confidence=0.9 if records else 0.0,
            explanation=self._ranking_explanation(records, "overall", graduation_year=degree.graduation_year),
            evidence=self._ranking_evidence(records, degree.graduation_year),
        )

    def _subject_university(self, degree: Degree | None, field_text: str | None) -> ComponentScore:
        if degree is None:
            return self._unavailable_component("subject_university_strength", "No degree institution is available.")
        subject = primary_subject(degree.field, degree.specialization)
        if subject is None:
            return ComponentScore(
                name="subject_university_strength",
                label=self.rubric["components"]["subject_university_strength"]["label"],
                score=None,
                weight=self.rubric["components"]["subject_university_strength"]["weight"],
                confidence=0.0,
                status="not_applicable",
                explanation="No subject could be mapped from the documented degree field.",
                evidence=[],
            )
        score, records = self.rankings.combined_standardized_score(degree.institution, "subject", subject)
        if score is None:
            return ComponentScore(
                name="subject_university_strength",
                label=self.rubric["components"]["subject_university_strength"]["label"],
                score=None,
                weight=self.rubric["components"]["subject_university_strength"]["weight"],
                confidence=0.0,
                status="not_found",
                explanation=f"No {subject} subject ranking found for {resolve_university(degree.institution).canonical}. Missing ranking evidence is not treated as zero.",
                evidence=[],
            )
        return ComponentScore(
            name="subject_university_strength",
            label=self.rubric["components"]["subject_university_strength"]["label"],
            score=round(score, 2),
            weight=self.rubric["components"]["subject_university_strength"]["weight"],
            confidence=0.9,
            explanation=self._ranking_explanation(records, "subject", subject, degree.graduation_year),
            evidence=self._ranking_evidence(records, degree.graduation_year),
        )

    def _dissertation(self, degree: Degree | None, job: JobRequirements) -> ComponentScore:
        if degree is None or degree.degree_type not in ("PhD", "Doctorate"):
            return ComponentScore(
                name="dissertation_relevance",
                label=self.rubric["components"]["dissertation_relevance"]["label"],
                score=None,
                weight=self.rubric["components"]["dissertation_relevance"]["weight"],
                confidence=0.0,
                status="not_applicable",
                explanation="Dissertation relevance applies to doctoral degrees; none was documented.",
                evidence=[],
            )
        if not degree.thesis_title:
            return ComponentScore(
                name="dissertation_relevance",
                label=self.rubric["components"]["dissertation_relevance"]["label"],
                score=None,
                weight=self.rubric["components"]["dissertation_relevance"]["weight"],
                confidence=0.0,
                status="not_found",
                explanation="Dissertation information not available. Missing evidence is not treated as unrelated.",
                evidence=[],
            )
        bucket, confidence = classify_relevance(degree.thesis_title, job.required_fields, job.research_areas, job.required_skills)
        score = relevance_score(bucket)
        explanation = (
            f"Dissertation '{degree.thesis_title}' is {relevance_label(bucket)} "
            f"relative to the job research areas {', '.join(job.research_areas) if job.research_areas else '(none documented)'}."
        )
        return ComponentScore(
            name="dissertation_relevance",
            label=self.rubric["components"]["dissertation_relevance"]["label"],
            score=score,
            weight=self.rubric["components"]["dissertation_relevance"]["weight"],
            confidence=confidence,
            explanation=explanation,
            evidence=[_evidence("DIS-001", degree.thesis_title, "CV", source_location=", ".join(str(p) for p in degree.source_pages), confidence=confidence)],
        )

    def _progression(self, facts: EducationFacts) -> ComponentScore:
        if not facts.degrees:
            return self._unavailable_component("academic_progression", "No education history is documented.")
        ordered = sorted(facts.degrees, key=lambda d: (d.start_year or d.graduation_year or 0, d.graduation_year or 0))
        years = [d.graduation_year for d in ordered]
        contradictions = any(ordered[i].start_year and ordered[i + 1].start_year and ordered[i].start_year > ordered[i + 1].start_year for i in range(len(ordered) - 1))
        missing_years = sum(1 for d in ordered if d.start_year is None or d.graduation_year is None)
        if contradictions:
            score, explanation = 4.0, "Major inconsistencies were found in the documented education timeline."
        elif missing_years >= len(ordered) // 2 + 1:
            score, explanation = 6.0, "Some education dates are missing, so progression could not be fully reconstructed."
        elif missing_years:
            score, explanation = 8.5, "Education history is mostly clear, with some missing dates."
        else:
            score, explanation = 10.0, "Education history is clear, coherent, and well documented."
        if facts.education_gaps:
            explanation += " Education gaps were flagged without inferring reasons for them."
        return ComponentScore(
            name="academic_progression",
            label=self.rubric["components"]["academic_progression"]["label"],
            score=score,
            weight=self.rubric["components"]["academic_progression"]["weight"],
            confidence=0.95 if not missing_years and not contradictions else 0.7,
            explanation=explanation,
            evidence=[_evidence("PROG-001", explanation, "CV", confidence=0.8)],
        )

    def _honors(self, facts: EducationFacts) -> ComponentScore:
        if not facts.honors:
            return ComponentScore(
                name="academic_honors",
                label=self.rubric["components"]["academic_honors"]["label"],
                score=None,
                weight=self.rubric["components"]["academic_honors"]["weight"],
                confidence=0.0,
                status="not_found",
                explanation="No academic honors or distinctions were documented. Absence from the CV is not proof of no awards.",
                evidence=[],
            )
        exceptional = sum(1 for h in facts.honors if h.significance == "exceptional")
        significant = sum(1 for h in facts.honors if h.significance == "significant")
        if exceptional:
            score = 10.0
        elif significant >= 2:
            score = 9.0
        elif significant == 1:
            score = 7.0
        else:
            score = 5.0
        explanation = f"Documented distinctions: {exceptional} exceptional, {significant} significant, {len(facts.honors) - exceptional - significant} minor."
        return ComponentScore(
            name="academic_honors",
            label=self.rubric["components"]["academic_honors"]["label"],
            score=score,
            weight=self.rubric["components"]["academic_honors"]["weight"],
            confidence=0.9,
            explanation=explanation,
            evidence=[_evidence(f"HON-{i + 1:03d}", h.title, "CV", source_location=", ".join(str(p) for p in h.source_pages), confidence=0.8) for i, h in enumerate(facts.honors[:5])],
        )

    def _additional_education(self, facts: EducationFacts, job: JobRequirements) -> ComponentScore:
        relevant = []
        for item in facts.additional_education:
            bucket, _ = classify_relevance(item.field, job.required_fields, job.research_areas, job.required_skills)
            if bucket in ("direct_match", "very_closely_related", "related"):
                relevant.append(item)
        if not facts.additional_education:
            return ComponentScore(
                name="additional_relevant_education",
                label=self.rubric["components"]["additional_relevant_education"]["label"],
                score=None,
                weight=self.rubric["components"]["additional_relevant_education"]["weight"],
                confidence=0.0,
                status="not_found",
                explanation="No additional education was documented.",
                evidence=[],
            )
        if not relevant:
            score = 2.0
            explanation = "Additional education was documented but was not clearly relevant to this position."
        elif len(relevant) >= 2:
            score = 8.0
            explanation = "Multiple additional education records are relevant to this position."
        else:
            score = 6.0
            explanation = "One additional education record is relevant to this position."
        return ComponentScore(
            name="additional_relevant_education",
            label=self.rubric["components"]["additional_relevant_education"]["label"],
            score=score,
            weight=self.rubric["components"]["additional_relevant_education"]["weight"],
            confidence=0.8,
            explanation=explanation,
            evidence=[_evidence("ADD-001", explanation, "CV", confidence=0.8)],
        )

    def _unavailable_component(self, name: str, explanation: str) -> ComponentScore:
        return ComponentScore(
            name=name,
            label=self.rubric["components"][name]["label"],
            score=None,
            weight=self.rubric["components"][name]["weight"],
            confidence=0.0,
            status="not_found",
            explanation=explanation,
            evidence=[],
        )

    def _combine(self, components: dict[str, ComponentScore]) -> tuple[float | None, list[CalculationLine]]:
        lines: list[CalculationLine] = []
        total_weight = 0.0
        weighted = 0.0
        for component in components.values():
            contribution = None
            if component.score is not None:
                contribution = component.score * component.weight
                weighted += contribution
                total_weight += component.weight
            lines.append(
                CalculationLine(
                    component=component.name,
                    score=component.score,
                    weight=component.weight,
                    contribution=contribution,
                )
            )
        if total_weight <= 0:
            return None, lines
        return weighted / total_weight, lines

    def _ranking_explanation(self, records: list[RankingRecord], ranking_type: str, subject: str | None = None, graduation_year: int | None = None) -> str:
        parts = []
        for record in records:
            rank = record.rank if record.rank is not None else "not ranked"
            year = record.ranking_year
            normalized = f"{record.standardized_score:.1f}" if record.standardized_score is not None else "N/A"
            label = f"{record.provider} {record.ranking_type}"
            if subject:
                label += f" {subject}"
            period = ranking_period_label(record.ranking_year, graduation_year)
            parts.append(f"{period}: {label} {year}: rank {rank} (standardized {normalized})")
        return " | ".join(parts) if parts else "No ranking evidence."

    def _ranking_evidence(self, records: list[RankingRecord], graduation_year: int | None = None) -> list[Evidence]:
        evidence = []
        for record in records:
            period = ranking_period_label(record.ranking_year, graduation_year)
            evidence.append(
                _evidence(
                    f"RANK-{record.provider}-{record.ranking_year}",
                    f"{period}: {record.institution} {record.provider} {record.ranking_type} {record.ranking_year}: rank {record.rank}",
                    record.provider,
                    source_url=record.source_url,
                    ranking_year=record.ranking_year,
                    confidence=0.95,
                    verified=False,
                    verification_status="preset",
                )
            )
        return evidence

    def _missing_information(self, components: dict[str, ComponentScore], facts: EducationFacts) -> list[str]:
        missing: list[str] = []
        for component in components.values():
            if component.score is None:
                missing.append(f"{component.label}: {component.explanation}")
        for gap in facts.education_gaps:
            missing.append(gap.note)
        return missing

    def _evidence_quality(self, components: dict[str, ComponentScore]) -> str:
        scored = [c for c in components.values() if c.score is not None]
        if not scored:
            return "LOW"
        average_confidence = sum(c.confidence for c in scored) / len(scored)
        missing = sum(1 for c in components.values() if c.score is None)
        if missing >= 3 or average_confidence < 0.55:
            return "LOW"
        if missing or average_confidence < 0.8:
            return "MEDIUM"
        return "HIGH"
