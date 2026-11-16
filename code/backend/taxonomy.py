"""One vocabulary for prompts, validation, local legacy conversion and the editor."""
import json
import re
import unicodedata
from pathlib import Path

VOCABULARY = json.loads((Path(__file__).resolve().parents[1] / "tags.json").read_text(encoding="utf-8"))


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def canonical(category, value):
    for label, aliases in VOCABULARY["aliases"].get(category, {}).items():
        if normalize(value) in {normalize(item) for item in [label, *aliases]}:
            return label
    return " ".join(value.split())


def legacy_level(degree):
    text = normalize(degree)
    if re.search(r"\b(postdoc|postdoctoral|honorary|non-degree)\b", text):
        return "Other"
    matches = set()
    for level, aliases in VOCABULARY["degree_aliases"].items():
        for alias in aliases:
            pattern = re.escape(normalize(alias))
            # Degree names start the field; do not classify a bachelor's transfer note
            # as a second qualification or match 'MS' inside a subject name.
            if re.match(r"^(?:degree of\s+)?" + pattern + r"(?:\b|(?<=\.))", text):
                matches.add(level)
    return next(iter(matches)) if len(matches) == 1 else "Unknown"


def legacy_status(record):
    text = normalize(f"{record.get('degree', '')} {record.get('period') or ''}")
    ongoing = bool(re.search(r"\b(present|ongoing|expected|pursuing|in progress|current)\b|在读|在讀", text))
    completed = bool(re.search(r"\b(completed|graduated|awarded|conferred)\b|已毕业|已畢業", text))
    # A past end year alone does not establish that a qualification was awarded.
    return "Ongoing" if ongoing and not completed else "Completed" if completed and not ongoing else "Unknown"
