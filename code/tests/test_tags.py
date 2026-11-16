import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import ai, main
from backend.profile_tools import profile_tags, validate_correction
from backend.schema import Profile
from backend.taxonomy import VOCABULARY, legacy_level, legacy_status, canonical


def dossier():
    return {"bio": {"name": "Example", "source_pages": [1]}, "education": [
        {"degree": "B.S. in Computing", "institution": "Example University", "period": "2016–2020", "source_pages": [1]},
        {"degree": "Ph.D. in Robotics", "institution": "Example University", "period": "2024–Present", "source_pages": [1]}],
        "work_experience": [], "publications": [], "achievements_awards": []}


class TagTests(unittest.TestCase):
    def test_conservative_legacy_levels(self):
        examples = {"B.S.": "Bachelor", "B.E. in Aircraft Design": "Bachelor",
                    "Bachelor's degree, Computing (transfer to AI approved)": "Bachelor",
                    "M.S. in Flight Dynamics": "Master", "Ph.D. in Robotics": "PhD",
                    "Vocational Degree in Software Engineering": "Vocational", "博士": "PhD",
                    "Postdoctoral fellow": "Other", "Honorary Ph.D.": "Other",
                    "Summer course in PhD research": "Unknown", "Diploma": "Unknown"}
        for original, expected in examples.items():
            with self.subTest(original=original):
                self.assertEqual(legacy_level(original), expected)

    def test_status_does_not_assume_graduation_from_dates(self):
        self.assertEqual(legacy_status({"degree": "B.S.", "period": "2015–2019"}), "Unknown")
        self.assertEqual(legacy_status({"degree": "PhD", "period": "2024–Present"}), "Ongoing")
        self.assertEqual(legacy_status({"degree": "B.S. (awarded 2020)"}), "Completed")
        self.assertEqual(legacy_status({"degree": "PhD (expected 2027)"}), "Ongoing")

    def test_aliases_are_exact_and_do_not_merge_distinct_institutions(self):
        self.assertEqual(canonical("Institution", "The Chinese University of Hong Kong (Shenzhen)"), "CUHK-Shenzhen")
        self.assertEqual(canonical("Institution", "The Chinese University of Hong Kong"), "The Chinese University of Hong Kong")
        self.assertEqual(canonical("Research interest", "  llm  "), "Large Language Models")
        self.assertEqual(canonical("Research interest", "LLM Benchmarking"), "LLM Benchmarking")

    def test_tags_keep_status_and_evidence_attached_to_degree(self):
        raw = dossier()
        raw["education"][0].update(degree_level="Bachelor", degree_status="Completed")
        profile = Profile.model_validate(raw).model_dump()
        tags = profile_tags(profile)
        labels = {tag["label"] for tag in tags}
        self.assertIn("Bachelor · Completed", labels)
        self.assertIn("PhD · Ongoing", labels)
        self.assertNotIn("PhD · Completed", labels)
        self.assertNotIn("B.S. in Computing", labels)
        phd = next(tag for tag in tags if tag["label"] == "PhD")
        self.assertEqual(phd["evidence"][0]["record"], 1)
        self.assertEqual(phd["evidence"][0]["source_pages"], [1])
        self.assertEqual(phd["evidence"][0]["original"], "Ph.D. in Robotics")

    def test_llm_requires_tags_but_legacy_read_still_works(self):
        raw = dossier()
        self.assertEqual(Profile.model_validate(raw).education[0].degree_level, "Bachelor")
        with self.assertRaisesRegex(ValueError, "degree_level and degree_status"):
            ai.validate_profile(json.dumps(raw), [{"number": 1}])
        result = Profile.model_validate(raw).model_dump()
        self.assertEqual(ai.validate_profile(json.dumps(result), [{"number": 1}]).education[1].degree_status, "Ongoing")
        result["education"][0]["degree_level"] = "B.S."
        with self.assertRaises(ValidationError):
            ai.validate_profile(json.dumps(result), [{"number": 1}])
        with self.assertRaises(ValidationError):
            validate_correction(result, "education", {1})

    def test_prompt_and_schema_use_shared_vocabulary(self):
        system = ai.profile_messages([{"number": 1, "text": "Evidence"}])[0]["content"]
        self.assertIn(json.dumps(VOCABULARY, ensure_ascii=False), system)
        fields = Profile.model_json_schema()["$defs"]["Education"]["properties"]
        self.assertEqual(fields["degree_level"]["enum"], VOCABULARY["degree_level"])

    def test_legacy_search_and_export_do_not_rewrite_saved_model_output(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(main, "STORAGE", Path(temporary)):
            with TestClient(main.app) as client:
                job = client.post("/api/profiles", json={"name": "Example"}).json()
                main.update_job(job["id"], analyzed_revision=job["revision"], status="completed")
                path = main.folder(job["id"]) / "profile.json"
                main.write_json(path, dossier())
                before = path.read_bytes()
                with patch.object(main, "generate_profile", AsyncMock()) as model:
                    self.assertEqual(client.get("/api/tag-vocabulary").json(), VOCABULARY)
                    result = client.get("/api/tag-search").json()
                    self.assertIn("Bachelor", [tag["label"] for tag in result["tags"]])
                    exported = client.get(f"/api/profiles/{job['id']}/json").json()
                    self.assertEqual(exported["education"][0]["degree_level"], "Bachelor")
                    model.assert_not_awaited()
                self.assertEqual(before, path.read_bytes())


class TagCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_tags_use_the_same_single_analysis_call(self):
        result = Profile.model_validate(dossier()).model_dump()
        with patch.object(ai, "call_json", AsyncMock(return_value=(json.dumps(result), {"calls": 1}))) as call:
            profile, receipt = await ai.generate_profile([{"number": 1, "text": "Education evidence"}])
            call.assert_awaited_once()
            self.assertEqual(receipt["calls"], 1)
            self.assertEqual(profile.education[1].degree_level, "PhD")
