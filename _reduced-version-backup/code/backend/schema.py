from pydantic import BaseModel, ConfigDict, Field


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
    institution: str
    department: str | None = None
    field: str | None = None
    period: str | None = None
    advisor: str | None = None
    thesis: str | None = None


class Experience(Record):
    role: str
    organization: str
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


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bio: Bio
    education: list[Education]
    work_experience: list[Experience]
    achievements_awards: list[Award]
    publications: list[Publication]
