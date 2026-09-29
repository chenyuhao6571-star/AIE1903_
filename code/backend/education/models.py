from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    claim: str
    source_type: str
    source_location: str | None = None
    source_text: str | None = None
    source_url: str | None = None
    ranking_year: int | None = None
    retrieved_at: str | None = None
    confidence: float | None = None
    verified: bool = False


class Degree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    degree_type: str
    field: str | None = None
    specialization: str | None = None
    institution: str
    country: str | None = None
    start_year: int | None = None
    graduation_year: int | None = None
    thesis_title: str | None = None
    status: str = "completed"
    source_pages: list[int] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class Honor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    year: str | None = None
    organization: str | None = None
    significance: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class AdditionalEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    field: str | None = None
    institution: str | None = None
    year: int | None = None
    relevance: str | None = None
    source_pages: list[int] = Field(default_factory=list)


class EducationGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_year: int | None = None
    end_year: int | None = None
    duration_years: int | None = None
    note: str = "Education gap identified. Reason not stated."
    inferred: bool = False


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    name: str | None = None
    current_title: str | None = None
    current_institution: str | None = None


class EducationFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: Candidate
    degrees: list[Degree] = Field(default_factory=list)
    honors: list[Honor] = Field(default_factory=list)
    additional_education: list[AdditionalEducation] = Field(default_factory=list)
    education_gaps: list[EducationGap] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    dissertation_status: str | None = None


class JobRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    minimum_degree: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    research_areas: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    qualification_text: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class RankingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str
    provider: str
    ranking_type: str
    subject: str | None = None
    ranking_year: int
    rank: int | None = None
    total_ranked: int | None = None
    raw_score: float | None = None
    standardized_score: float | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    status: str = "found"


class QualificationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_degree_met: bool | None = None
    field_requirement_met: bool | None = None
    status: str = "UNKNOWN"
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


class ComponentScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    score: float | None
    weight: float
    confidence: float
    status: str = "scored"
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


class CalculationLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    score: float | None
    weight: float
    contribution: float | None


class EducationScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_score: float | None = None
    components: dict[str, ComponentScore] = Field(default_factory=dict)
    calculation: list[CalculationLine] = Field(default_factory=list)
    external_evidence_incomplete: bool = False


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str | None = None
    original_score: float | None = None
    reviewer_score: float | None = None
    reviewer_id: str | None = None
    reason: str | None = None
    note: str | None = None
    timestamp: str = Field(default_factory=utc_now)


class EducationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    job_id: str
    candidate_name: str | None = None
    position_title: str | None = None
    qualification_check: QualificationCheck
    education_score: EducationScore
    evidence_quality: str = "UNKNOWN"
    missing_information: list[str] = Field(default_factory=list)
    review_status: str = "PENDING"
    rubric_version: str = "education_v1.0"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    audit_trail: list[ReviewAction] = Field(default_factory=list)
