from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=None)
def load_json(name: str) -> dict | list:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def rubric() -> dict:
    return load_json("education_rubric.json")  # type: ignore[return-value]


def ranking_scoring() -> dict:
    return load_json("ranking_scoring.json")  # type: ignore[return-value]


def subject_mapping() -> dict:
    return load_json("subject_mapping.json")  # type: ignore[return-value]


def university_aliases() -> dict:
    return load_json("university_aliases.json")  # type: ignore[return-value]


def ranking_fixtures() -> list[dict]:
    return load_json("ranking_fixtures.json")  # type: ignore[return-value]
