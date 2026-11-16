from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from .taxonomy import VOCABULARY, legacy_level, legacy_status


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_pages: list[int] = Field(default_factory=list)


class Bio(Record):
    name: str
    current_position: str | None = None
    institution: str | None = None
    email: str | None = None
    website: str | None = None
    summary: str | None = None
    research_interests: list[str] = Field(default_factory=list)


class Education(Record):
    degree: str
    degree_level: str = Field(json_schema_extra={"enum": VOCABULARY["degree_level"]})
    degree_status: str = Field(json_schema_extra={"enum": VOCABULARY["degree_status"]})
    institution: str
    department: str | None = None
    period: str | None = None
    advisor: str | None = None
    thesis: str | None = None

    @model_validator(mode="before")
    @classmethod
    def convert_legacy(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "degree_level" not in data:
                data["degree_level"] = legacy_level(data.get("degree", "")) if isinstance(data.get("degree", ""), str) else "Unknown"
            if "degree_status" not in data:
                data["degree_status"] = legacy_status(data)
        return data

    @field_validator("degree_level", "degree_status")
    @classmethod
    def valid_tag(cls, value, info):
        if value not in VOCABULARY[info.field_name]:
            raise ValueError(f"Choose a permitted {info.field_name} from tags.json.")
        return value


class Experience(Record):
    role: str
    organization: str | None = None
    period: str | None = None
    description: str | None = None


class Award(Record):
    title: str
    year: str | None = None
    organization: str | None = None
    description: str | None = None


class Publication(Record):
    title: str
    year: str | None = None
    venue: str | None = None
    authors: str | None = None
    authorship: str | None = None
    status: str | None = None
    doi: str | None = None
    url: str | None = None


class Statement(Record):
    title: str
    description: str


class LinkAnalysis(Statement):
    url: str = Field(max_length=300)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bio: Bio
    education: list[Education]
    work_experience: list[Experience]
    achievements_awards: list[Award]
    publications: list[Publication]
    teaching: list[Statement] = Field(default_factory=list)
    research: list[Statement] = Field(default_factory=list)
    motivation: list[Statement] = Field(default_factory=list)
    links: list[LinkAnalysis] = Field(default_factory=list)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ScoreSection(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    title: str = Field(min_length=1, max_length=100)
    max_score: float = Field(gt=0, le=1000)
    rules: str = Field(min_length=10, max_length=6000)


class Rubric(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    sections: list[ScoreSection] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_sections(self):
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Section IDs must be unique.")
        if sum(s.max_score for s in self.sections) > 1000:
            raise ValueError("The total maximum score must not exceed 1000.")
        return self


class Evidence(StrictModel):
    source_id: int = Field(gt=0)
    quote: str = Field(min_length=5, max_length=800)


class SectionScore(StrictModel):
    section_id: str
    score: float = Field(ge=0, le=1000)
    status: Literal["supported", "insufficient_evidence"]
    rationale: str = Field(min_length=1, max_length=600)
    evidence: list[Evidence] = Field(max_length=30)
    gaps: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(max_length=5)


class ScoreResponse(StrictModel):
    sections: list[SectionScore]
