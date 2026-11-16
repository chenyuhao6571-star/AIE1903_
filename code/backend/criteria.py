"""File-backed scoring templates. Shipped examples can be overridden locally."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from fastapi import HTTPException

from .schema import Rubric

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(rubric):
    return hashlib.sha256(rubric.model_dump_json().encode()).hexdigest()


def template_path(storage, template_id):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", template_id or ""):
        raise HTTPException(404, "Criteria template not found.")
    custom = storage / "criteria" / (template_id + ".json")
    if custom.is_file():
        return custom
    shipped = ROOT / "criteria" / (template_id + ".json")
    if template_id == "academic":
        shipped = ROOT / "score.json"
    if not shipped.is_file():
        raise HTTPException(404, "Criteria template not found.")
    return shipped


def get_template(storage, template_id):
    path = template_path(storage, template_id)
    try:
        return Rubric.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise HTTPException(400, "Invalid criteria template. Correct its sections, point limits and rules.") from None


def describe(template_id, rubric):
    return {"id": template_id, **rubric.model_dump(), "rubric_hash": fingerprint(rubric),
            "max_score": sum(s.max_score for s in rubric.sections)}


def list_templates(storage):
    ids = {"academic"}
    ids.update(p.stem for p in (ROOT / "criteria").glob("*.json"))
    ids.update(p.stem for p in (storage / "criteria").glob("*.json"))
    rows = [describe(template_id, get_template(storage, template_id)) for template_id in ids]
    return sorted(rows, key=lambda row: row["title"].casefold())


def save_template(storage, rubric, template_id=None):
    if template_id:
        template_path(storage, template_id)
    else:
        template_id = "custom_" + uuid.uuid4().hex
    directory = storage / "criteria"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (template_id + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rubric.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return describe(template_id, rubric)


def import_template(storage, rubric):
    # Re-importing the same file should reuse the existing template.
    for row in list_templates(storage):
        if row["rubric_hash"] == fingerprint(rubric):
            return row
    return save_template(storage, rubric)
