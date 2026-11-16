"""Deterministic tags and validation for human profile corrections."""
import unicodedata

from .schema import Profile
from .taxonomy import canonical


def normalize_tag(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def profile_tags(profile):
    tags = {}

    def add(category, value, section, index, record):
        if not value or not value.strip():
            return
        label = canonical(category, value)
        key = f"{category}:{normalize_tag(label)}"
        tag = tags.setdefault(key, {"id": key, "category": category, "label": label, "evidence": []})
        evidence = {"section": section, "record": index, "source_pages": record["source_pages"],
                    "original": record["degree"] if category in {"Degree", "Degree status"} else value}
        if evidence not in tag["evidence"]:
            tag["evidence"].append(evidence)

    bio = profile["bio"]
    for interest in bio["research_interests"]:
        add("Research interest", interest, "bio", 0, bio)
    add("Institution", bio["institution"], "bio", 0, bio)
    add("Role", bio["current_position"], "bio", 0, bio)
    for index, record in enumerate(profile["education"]):
        add("Institution", record["institution"], "education", index, record)
        add("Degree", record["degree_level"], "education", index, record)
        # Keep status attached to its qualification: a completed bachelor must not
        # make an ongoing PhD appear completed when filtering candidates.
        add("Degree status", f"{record['degree_level']} · {record['degree_status']}", "education", index, record)
    for index, record in enumerate(profile["work_experience"]):
        add("Institution", record["organization"], "work_experience", index, record)
        add("Role", record["role"], "work_experience", index, record)
    for index, record in enumerate(profile["publications"]):
        add("Venue", record["venue"], "publications", index, record)
    return sorted(tags.values(), key=lambda tag: (tag["category"], normalize_tag(tag["label"])))


def validate_correction(profile, section, source_ids):
    result = Profile.model_validate(profile).model_dump()
    records = [result[section]] if section == "bio" else result[section]
    required = {"bio": ["name"], "education": ["degree", "institution"],
                "work_experience": ["role"], "publications": ["title"],
                "achievements_awards": ["title"], "teaching": ["title", "description"],
                "research": ["title", "description"], "motivation": ["title", "description"],
                "links": ["title", "description", "url"]}
    for index, record in enumerate(records, 1):
        for field in required[section]:
            if not record[field].strip():
                raise ValueError(f"Record {index}: {field.replace('_', ' ')} is required.")
        if not record["source_pages"] or not set(record["source_pages"]).issubset(source_ids):
            raise ValueError(f"Record {index}: select at least one source from the current dossier.")
    if any(not interest.strip() for interest in result["bio"]["research_interests"]):
        raise ValueError("Research interests cannot be blank.")
    return result
