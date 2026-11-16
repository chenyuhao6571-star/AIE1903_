import asyncio
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from docx import Document
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import ai, main
from backend.ai import AIError
from backend.extraction import DocumentError, extract_document
from backend.schema import Profile, Rubric, ScoreResponse


def word_bytes(text=None):
    doc = Document()
    doc.add_paragraph(text or "Alex Example: researcher in software testing and program verification.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Education"
    table.cell(0, 1).text = "PhD, Example University, 2020"
    doc.add_paragraph("Work experience: Research Fellow at Example Institute, 2020-present.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


PROFILE = Profile.model_validate({
    "bio": {"name": "Alex Example", "source_pages": [1]},
    "education": [], "work_experience": [], "achievements_awards": [], "publications": [],
})
RECEIPT = {"calls": 1, "input_tokens": 1000, "output_tokens": 500, "cost_usd": .0009}
RUBRIC = Rubric.model_validate({"title": "Qualification review", "sections": [
    {"id": "education", "title": "Education", "max_score": 30, "rules": "Award 30 for a documented completed PhD; otherwise zero."}]})
SCORE = {"sections": [{"section_id": "education", "score": 30, "status": "supported", "rationale": "Documented PhD.",
                       "evidence": [{"source_id": 2, "quote": "PhD, Example University, 2020"}], "gaps": []}]}


class ExtractionTests(unittest.TestCase):
    @unittest.skipUnless(main.SAMPLE.is_file(), "Optional local sample CV is not included in Git")
    def test_real_cv_has_readable_pages(self):
        pages = extract_document(main.SAMPLE.read_bytes(), "CV.pdf")
        self.assertEqual(len(pages), 7)
        self.assertIn("Jialun Cao", pages[0]["text"])

    def test_word_preserves_paragraph_and_table_order(self):
        pages = extract_document(word_bytes(), "CV.docx")
        self.assertEqual(len(pages), 3)
        self.assertIn("Education | PhD", pages[1]["text"])
        self.assertIn("Work experience", pages[2]["text"])

    def test_unreadable_files_and_empty_docs(self):
        for data, name in [(b"corrupt", "cv.pdf"), (b"legacy", "cv.doc"), (b"", "cv.pdf")]:
            with self.subTest(name=name), self.assertRaises(DocumentError):
                extract_document(data, name)
        buffer = io.BytesIO()
        Document().save(buffer)
        with self.assertRaisesRegex(DocumentError, "No readable text"):
            extract_document(buffer.getvalue(), "empty.docx")


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(main, "STORAGE", Path(self.temp.name))
        self.storage.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.job_id = self.client.post("/api/profiles", json={"name": "Alex"}).json()["id"]
        self.url = f"/api/profiles/{self.job_id}"

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.storage.stop()
        self.temp.cleanup()

    def upload(self, docs=None):
        docs = docs or [("CV.docx", word_bytes())]
        return self.client.post(self.url + "/documents", files=[("files", (name, data)) for name, data in docs])

    def wait_job(self, operation="analysis"):
        field = "status" if operation == "analysis" else "score_status"
        for _ in range(100):
            job = self.client.get(self.url).json()
            if job[field] in ("completed", "failed"):
                return job
            time.sleep(.025)
        self.fail("Job did not finish")

    def run_action(self, operation="analysis"):
        estimate = self.client.get(self.url + f"/estimate?operation={operation}").json()
        return self.client.post(self.url + ("/analyze" if operation == "analysis" else "/score"),
                                json={"revision": estimate["revision"], "rubric_hash": estimate.get("rubric_hash")})

    def analyze(self):
        with patch.object(main, "generate_profile", AsyncMock(return_value=(PROFILE, RECEIPT))):
            self.assertEqual(self.run_action().status_code, 202)
            return self.wait_job()

    def set_rubric(self, rubric=None):
        return self.client.put(self.url + "/rubric", files={"file": ("score.json", (rubric or RUBRIC).model_dump_json())})

    def test_manual_profile_correction_updates_search_export_and_preserves_original(self):
        self.upload()
        job = self.analyze()
        original = main.read_json(main.folder(self.job_id) / "profile.json")
        bio = {**job["profile"]["bio"], "name": "Alex Corrected", "research_interests": ["Machine Learning", " machine learning "]}
        with patch.object(main, "generate_profile", AsyncMock()) as model:
            result = self.client.put(self.url + "/profile", json={"revision": job["revision"], "edit_version": 0, "section": "bio", "value": bio})
            self.assertEqual(result.status_code, 200, result.text)
            model.assert_not_awaited()
        edited = self.client.get(self.url).json()
        self.assertEqual(edited["profile_edit_version"], 1)
        self.assertEqual(edited["name"], "Alex Corrected")
        self.assertEqual(self.client.get(self.url + "/json").json()["bio"]["name"], "Alex Corrected")
        self.assertEqual(main.read_json(main.folder(self.job_id) / "profile.json"), original)
        audit = main.read_json(next((main.folder(self.job_id) / "profile-edits").glob("*.json")))
        self.assertEqual(audit["before"], original["bio"])
        self.assertEqual(audit["after"], bio)
        search = self.client.get("/api/tag-search").json()
        self.assertEqual(len(search["tags"]), 1)
        self.assertEqual(search["tags"][0]["count"], 1)
        self.assertTrue(search["rows"][0]["manually_edited"])
        self.upload([("New.docx", word_bytes("Additional research evidence in a new document."))])
        stale = self.client.get(self.url).json()
        self.assertIsNone(stale["profile"])
        self.assertEqual(stale["profile_edit_version"], 0)
        self.assertEqual(self.client.get("/api/tag-search").json()["rows"], [])

    def test_corrections_reject_stale_invalid_and_busy_requests(self):
        self.upload()
        job = self.analyze()
        payload = {"revision": job["revision"], "edit_version": 0, "section": "bio", "value": job["profile"]["bio"]}
        for changes, status in [({"revision": 999}, 409), ({"edit_version": 99}, 409),
                                ({"value": {**payload["value"], "source_pages": [999]}}, 422),
                                ({"value": {**payload["value"], "name": " "}}, 422),
                                ({"value": {**payload["value"], "source_pages": []}}, 422),
                                ({"section": "not_a_section"}, 422)]:
            self.assertEqual(self.client.put(self.url + "/profile", json={**payload, **changes}).status_code, status)
        valid = {**payload, "value": {**payload["value"], "summary": "Corrected summary."}}
        self.assertEqual(self.client.put(self.url + "/profile", json=valid).status_code, 200)
        self.assertEqual(self.client.put(self.url + "/profile", json=valid).status_code, 409)
        main.update_job(self.job_id, score_status="analyzing")
        self.assertEqual(self.client.put(self.url + "/profile", json={**valid, "edit_version": 1}).status_code, 409)

    def test_notes_work_before_analysis_and_do_not_change_evidence(self):
        self.upload()
        before = main.sources(main.read_job(self.job_id))
        result = self.client.put(self.url + "/notes", json={"version": 0, "text": "Discuss teaching at interview."})
        self.assertEqual(result.status_code, 200)
        job = self.client.get(self.url).json()
        self.assertEqual(job["notes"], "Discuss teaching at interview.")
        self.assertEqual(job["notes_version"], 1)
        self.assertEqual(before, main.sources(job))
        self.assertEqual(self.client.put(self.url + "/notes", json={"version": 0, "text": "Stale"}).status_code, 409)
        self.assertEqual(self.client.put(self.url + "/notes", json={"version": 1, "text": "x" * 20001}).status_code, 422)
        self.assertEqual(self.client.put(self.url + "/notes", json={"version": 1, "text": ""}).status_code, 200)

    def test_delete_candidate_removes_records_and_blocks_active_requests(self):
        self.upload()
        self.analyze()
        path = main.folder(self.job_id)
        main.update_job(self.job_id, score_status="queued")
        self.assertEqual(self.client.delete(self.url).status_code, 409)
        self.assertTrue(path.is_dir())
        main.update_job(self.job_id, score_status="none")
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url + "/json").status_code, 404)
        self.assertEqual(self.client.get("/api/profiles").json(), [])
        self.assertEqual(self.client.get("/api/tag-search").json()["rows"], [])
        self.assertEqual(self.client.delete(self.url).status_code, 404)

    def test_unnamed_consulting_organization_survives_analysis_search_and_manual_edit(self):
        self.upload()
        data = PROFILE.model_dump()
        data["work_experience"] = [{"role": "Industry Consultant", "organization": None,
                                    "description": "Documented consulting role.", "source_pages": [1]}]
        with patch.object(ai, "call_json", AsyncMock(return_value=(json.dumps(data), RECEIPT))) as model:
            self.assertEqual(self.run_action().status_code, 202)
            job = self.wait_job()
            self.assertEqual(job["status"], "completed", job.get("error"))
            model.assert_awaited_once()
        self.assertIsNone(job["profile"]["work_experience"][0]["organization"])
        self.assertEqual(self.client.get(self.url + "/json").json()["work_experience"], job["profile"]["work_experience"])
        tags = self.client.get("/api/tag-search").json()["tags"]
        self.assertIn("Industry Consultant", [tag["label"] for tag in tags])
        self.assertNotIn("Institution", [tag["category"] for tag in tags])
        value = [{**job["profile"]["work_experience"][0], "description": "Manually corrected description."}]
        response = self.client.put(self.url + "/profile", json={"revision": job["revision"], "edit_version": 0,
                                                                 "section": "work_experience", "value": value})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.client.get(self.url).json()["profile"]["work_experience"][0]["organization"])

    def test_nullable_organization_still_rejects_invalid_types_and_sources(self):
        data = PROFILE.model_dump()
        data["work_experience"] = [{"role": "Consultant", "organization": {"name": "Invented"}, "source_pages": [1]}]
        with self.assertRaises(ValidationError):
            ai.validate_profile(json.dumps(data), [{"number": 1}])
        data["work_experience"][0]["organization"] = None
        data["work_experience"][0]["source_pages"] = [999]
        with self.assertRaisesRegex(ValueError, "invalid source IDs"):
            ai.validate_profile(json.dumps(data), [{"number": 1}])

    def test_multiple_uploads_make_zero_calls_then_one_explicit_analysis(self):
        with patch.object(main, "generate_profile", AsyncMock(return_value=(PROFILE, RECEIPT))) as model:
            self.assertEqual(self.upload().status_code, 201)
            self.assertEqual(self.upload([("Teaching.docx", word_bytes("Alex Example teaches software verification using project-based learning."))]).status_code, 201)
            model.assert_not_awaited()
            draft = self.client.get(self.url).json()
            self.assertEqual(draft["status"], "draft")
            self.assertIsNone(draft["profile"])
            estimate = self.client.get(self.url + "/estimate").json()
            model.assert_not_awaited()
            self.assertEqual(estimate["calls"], 1)
            self.assertGreater(estimate["high_estimate_usd"], estimate["estimated_usd"])
            self.assertEqual(self.run_action().status_code, 202)
            job = self.wait_job()
            self.assertEqual(job["profile"]["bio"]["name"], "Alex Example")
            model.assert_awaited_once()
            self.assertEqual(len(model.call_args.args[0]), 6)
            self.assertEqual(self.run_action().status_code, 409)
            model.assert_awaited_once()
            self.assertEqual(self.client.get(self.url + "/json").json(), PROFILE.model_dump())
            self.assertEqual(job["runs"][0]["usage"], RECEIPT)

    def test_sources_retain_document_identity_and_original_page(self):
        self.upload([("CV.docx", word_bytes()), ("Cover.docx", word_bytes("Alex Example applies to this role to develop reliable software systems."))])
        pages = self.client.get(self.url + "/text").json()
        self.assertEqual(len({p["number"] for p in pages}), 6)
        self.assertEqual(pages[3]["filename"], "Cover.docx")
        self.assertEqual(pages[3]["page_number"], 1)
        self.assertEqual(pages[3]["unit"], "block")
        self.assertNotEqual(pages[0]["document_id"], pages[3]["document_id"])
        doc_id = pages[0]["document_id"]
        self.assertEqual(self.client.get(self.url + f"/documents/{doc_id}").status_code, 200)

    def test_batch_is_atomic_and_rejects_duplicates(self):
        content = word_bytes()
        self.assertEqual(self.upload([("CV.docx", content), ("bad.pdf", b"broken")]).status_code, 400)
        self.assertEqual(self.client.get(self.url).json()["documents"], [])
        self.assertEqual(self.upload([("CV.docx", content), ("copy.docx", content)]).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()["documents"], [])
        self.upload([("CV.docx", content)])
        self.assertEqual(self.upload([("copy.docx", content)]).status_code, 409)
        self.assertEqual(len(self.client.get(self.url).json()["documents"]), 1)

    def test_stale_estimate_and_empty_candidate_do_not_launch(self):
        with patch.object(main, "generate_profile", AsyncMock()) as model:
            self.assertEqual(self.client.post(self.url + "/analyze", json={"revision": 0}).status_code, 400)
            self.upload()
            self.assertEqual(self.client.post(self.url + "/analyze", json={"revision": 0}).status_code, 409)
            model.assert_not_awaited()

    def test_manual_retry_only_after_failure(self):
        self.upload()
        with patch.object(main, "generate_profile", AsyncMock(side_effect=AIError("Invalid response", RECEIPT))) as model:
            self.run_action()
            job = self.wait_job()
            self.assertEqual(job["status"], "failed")
            model.assert_awaited_once()
            self.assertEqual(job["runs"][0]["usage"], RECEIPT)
        self.assertEqual(self.analyze()["status"], "completed")
        self.assertEqual(len(self.client.get(self.url).json()["runs"]), 2)

    def test_busy_candidate_blocks_mutation_and_duplicate_call(self):
        self.upload()
        async def slow(pages):
            await asyncio.sleep(.3)
            return PROFILE, RECEIPT
        with patch.object(main, "generate_profile", side_effect=slow):
            self.assertEqual(self.run_action().status_code, 202)
            self.assertEqual(self.run_action().status_code, 409)
            self.assertEqual(self.set_rubric().status_code, 409)
            doc = self.client.get(self.url).json()["documents"][0]
            self.assertEqual(self.client.delete(self.url + "/documents/" + doc["id"]).status_code, 409)
            self.wait_job()

    def test_score_requires_profile_and_matches_rubric(self):
        self.upload()
        self.assertEqual(self.client.get(self.url + "/estimate?operation=score").status_code, 409)
        self.analyze()
        self.set_rubric()
        with patch.object(main, "generate_score", AsyncMock(return_value=({"sections": [], "total": 30, "max_score": 30, "rubric": RUBRIC.model_dump()}, RECEIPT))) as model:
            self.assertEqual(self.run_action("score").status_code, 202)
            job = self.wait_job("score")
            self.assertEqual(job["score"]["total"], 30)
            model.assert_awaited_once()
            self.assertEqual(model.call_args.args[2], RUBRIC)
            self.assertEqual(self.run_action("score").status_code, 409)
            self.assertEqual(self.client.get(self.url + "/score.json").status_code, 200)
        unchanged = self.set_rubric()
        self.assertEqual(unchanged.status_code, 200)
        self.assertIsNotNone(self.client.get(self.url).json()["score"])
        self.upload([("Research.docx", word_bytes("Alex Example proposes research on trustworthy software testing methods."))])
        job = self.client.get(self.url).json()
        self.assertIsNone(job["profile"])
        self.assertIsNone(job["score"])
        self.assertEqual(job["status"], "draft")
        self.assertEqual(self.client.get(self.url + "/json").status_code, 404)
        self.assertEqual(self.client.get(self.url + "/score.json").status_code, 404)

    def test_changed_rubric_rejects_old_score_estimate(self):
        self.upload()
        self.analyze()
        self.set_rubric()
        old = self.client.get(self.url + "/estimate?operation=score").json()
        changed = RUBRIC.model_copy(deep=True)
        changed.sections[0].max_score = 40
        self.set_rubric(changed)
        response = self.client.post(self.url + "/score", json={"revision": old["revision"], "rubric_hash": old["rubric_hash"]})
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(self.client.get(self.url).json()["profile"])

    def test_remove_document_invalidates_profile_preserves_original(self):
        self.upload()
        job = self.analyze()
        doc = job["documents"][0]
        self.assertEqual(self.client.delete(self.url + "/documents/" + doc["id"]).status_code, 200)
        updated = self.client.get(self.url).json()
        self.assertEqual(updated["documents"], [])
        self.assertIsNone(updated["profile"])
        self.assertTrue((main.folder(self.job_id) / doc["file"]).is_file())
        self.assertEqual(self.client.get(self.url + "/documents/" + doc["id"]).status_code, 404)

    def test_invalid_rubric_does_not_replace_valid_file(self):
        self.set_rubric()
        for raw in ['{}', '{bad', json.dumps({"title": "Invalid", "sections": []}), 'x' * 100001]:
            response = self.client.put(self.url + "/rubric", files={"file": ("score.json", raw)})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url + "/rubric").json(), RUBRIC.model_dump())

    def test_combined_text_limit_does_not_silently_truncate(self):
        with patch.object(main, "MAX_TEXT", 10):
            self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.client.get(self.url).json()["documents"], [])

    def test_unknown_document_and_candidate(self):
        self.assertEqual(self.client.get(self.url + "/documents/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/profiles/not-a-job").status_code, 404)

    def test_profile_persists_across_restart_without_another_call(self):
        self.upload()
        self.analyze()
        self.client.__exit__(None, None, None)
        with patch.object(main, "generate_profile", AsyncMock()) as model:
            self.client = TestClient(main.app)
            self.client.__enter__()
            job = self.client.get(self.url).json()
            self.assertEqual(job["profile"]["bio"]["name"], "Alex Example")
            self.assertEqual(len(job["runs"]), 1)
            model.assert_not_awaited()

    def test_interrupted_request_is_not_automatically_restarted(self):
        self.upload()
        self.client.__exit__(None, None, None)
        main.update_job(self.job_id, status="analyzing", runs=[{"id": "interrupted", "operation": "analysis", "status": "running"}])
        with patch.object(main, "generate_profile", AsyncMock()) as model:
            self.client = TestClient(main.app)
            self.client.__enter__()
            job = self.client.get(self.url).json()
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["runs"][0]["status"], "interrupted")
            model.assert_not_awaited()

    def select(self, template_id):
        return self.client.put(self.url + "/template", json={"template_id": template_id})

    def save_fake_score(self, value):
        rubric = main.rubric_for(self.job_id)
        job = main.read_job(self.job_id)
        result = {"sections": [{"section_id": section.id, "score": value} for section in rubric.sections],
                  "total": value * len(rubric.sections), "max_score": sum(section.max_score for section in rubric.sections),
                  "rubric": rubric.model_dump(), "rubric_hash": main.rubric_hash(rubric),
                  "revision": job["revision"], "template_id": job["selected_template_id"], "created_at": main.now()}
        main.archive_score(self.job_id, result)
        main.write_json(main.folder(self.job_id) / "score.json", result)
        main.update_job(self.job_id, score_status="completed")
        return result

    def test_template_choice_is_required_even_after_analysis(self):
        self.upload()
        self.analyze()
        job = self.client.get(self.url).json()
        self.assertIsNone(job["selected_template_id"])
        self.assertIsNone(job["rubric"])
        with patch.object(main, "generate_score", AsyncMock()) as model:
            self.assertEqual(self.client.get(self.url + "/estimate?operation=score").status_code, 409)
            self.assertEqual(self.client.post(self.url + "/score", json={"revision": job["revision"]}).status_code, 409)
            model.assert_not_awaited()
        self.select("assistant_professor")
        self.assertEqual(self.client.get(self.url + "/estimate?operation=score").status_code, 200)
        self.assertEqual(self.select("does_not_exist").status_code, 404)
        self.assertEqual(self.client.get(self.url).json()["selected_template_id"], "assistant_professor")

    def test_saved_scores_survive_dossier_and_criteria_changes(self):
        self.upload()
        self.select("assistant_professor")
        first = self.save_fake_score(5)
        rows = self.client.get(self.url + "/scores").json()
        self.assertEqual(len(rows), 1)  # score.json is a duplicate, not another assessment.
        self.assertTrue(rows[0]["is_current"])
        first_id = rows[0]["id"]
        changed = main.rubric_for(self.job_id).model_copy(deep=True)
        changed.sections[0].rules += " Changed criterion."
        self.client.put("/api/templates/assistant_professor", json=changed.model_dump())
        self.upload([("Extra.docx", word_bytes("New teaching statement."))])
        second = self.save_fake_score(10)
        with patch.object(main, "generate_score", AsyncMock()) as model:
            rows = self.client.get(self.url + "/scores").json()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["total"], second["total"])
            self.assertFalse(next(row for row in rows if row["id"] == first_id)["is_current"])
            original = self.client.get(self.url + f"/scores/{first_id}").json()
            self.assertEqual(original, first)
            self.assertNotIn("source_snapshot", original)  # Never borrow current dossier sources.
            exported = self.client.get(self.url + f"/scores/{first_id}?download=true")
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(exported.json(), first)
            self.select(None)
            self.assertEqual(len(self.client.get(self.url + "/scores").json()), 2)
            self.assertEqual(self.client.get(self.url + "/scores/unknown").status_code, 404)
            other_id = self.client.post("/api/profiles", json={"name": "Other"}).json()["id"]
            self.assertEqual(self.client.get(f"/api/profiles/{other_id}/scores/{first_id}").status_code, 404)
            model.assert_not_awaited()

    def test_score_history_empty_and_legacy_fallback(self):
        self.assertEqual(self.client.get(self.url + "/scores").json(), [])
        self.select("dean")
        result = self.save_fake_score(5)
        for path in (main.folder(self.job_id) / "scores").glob("*.json"):
            path.unlink()
        rows = self.client.get(self.url + "/scores").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.client.get(self.url + f"/scores/{rows[0]['id']}").json(), result)

    def test_new_score_preserves_original_source_snapshot(self):
        self.upload()
        self.analyze()
        self.set_rubric()
        original_sources = main.sources(main.read_job(self.job_id))
        result = {**copy.deepcopy(SCORE), "rubric": RUBRIC.model_dump(), "total": 30, "max_score": 30}
        with patch.object(main, "generate_score", AsyncMock(return_value=(result, RECEIPT))):
            self.assertEqual(self.run_action("score").status_code, 202)
            self.wait_job("score")
        rows = self.client.get(self.url + "/scores").json()
        self.upload([("Extra.docx", word_bytes("Later document."))])
        saved = self.client.get(self.url + f"/scores/{rows[0]['id']}").json()
        self.assertEqual(saved["source_snapshot"], original_sources)
        self.assertTrue(saved["run_id"])

    def test_template_library_import_edit_download_persists_locally(self):
        library = self.client.get("/api/templates").json()
        self.assertTrue({"Assistant Professor", "USTF", "Dean"} <= {row["title"] for row in library})
        created = self.client.post("/api/templates", json=RUBRIC.model_dump()).json()
        duplicate = self.client.post("/api/templates", json=RUBRIC.model_dump()).json()
        self.assertEqual(created["id"], duplicate["id"])
        self.assertEqual(self.client.get(f"/api/templates/{created['id']}/json").json(), RUBRIC.model_dump())
        changed = RUBRIC.model_copy(deep=True)
        changed.sections[0].max_score = 40
        self.assertEqual(self.client.put(f"/api/templates/{created['id']}", json=changed.model_dump()).status_code, 200)
        self.assertEqual(main.criteria.get_template(main.STORAGE, created["id"]).sections[0].max_score, 40)
        original = (main.ROOT / "criteria" / "dean.json").read_bytes()
        self.assertEqual(self.client.put("/api/templates/dean", json=changed.model_dump()).status_code, 200)
        self.assertEqual((main.ROOT / "criteria" / "dean.json").read_bytes(), original)
        self.assertEqual(self.client.post("/api/templates", json={"title": "Broken", "sections": []}).status_code, 422)

    def test_template_switch_restores_score_and_comparison_uses_shared_criteria(self):
        self.upload()
        self.analyze()
        self.select("assistant_professor")
        first = self.save_fake_score(5)
        self.select("dean")
        second = self.save_fake_score(10)
        all_rows = self.client.get("/api/comparison").json()["rows"]
        self.assertEqual(all_rows[0]["total"], second["total"])
        filtered = self.client.get("/api/comparison?template_id=assistant_professor").json()
        self.assertEqual(filtered["rows"][0]["total"], first["total"])
        self.assertEqual(filtered["rows"][0]["template_title"], "Assistant Professor")
        with patch.object(main, "generate_score", AsyncMock()) as model:
            self.select("assistant_professor")
            self.assertEqual(self.client.get(self.url).json()["score"]["total"], first["total"])
            self.assertEqual(self.client.get(self.url + "/score.json").json()["total"], first["total"])
            self.assertEqual(self.run_action("score").status_code, 409)
            model.assert_not_awaited()
        self.upload([("Extra.docx", word_bytes("Additional statement about teaching and research by Alex Example."))])
        for template_id in ["assistant_professor", "dean"]:
            row = self.client.get(f"/api/comparison?template_id={template_id}").json()["rows"][0]
            self.assertIsNone(row["total"])
            self.assertEqual(row["status"], "Needs analysis")

    def test_template_edit_hides_old_scores_and_rejects_old_estimate(self):
        self.upload()
        self.analyze()
        self.select("dean")
        self.save_fake_score(0)
        old = self.client.get(self.url + "/estimate?operation=score").json()
        changed = main.rubric_for(self.job_id).model_copy(deep=True)
        changed.sections[0].rules += " Award no points without documented responsibility."
        self.client.put("/api/templates/dean", json=changed.model_dump())
        self.assertIsNone(self.client.get(self.url).json()["score"])
        self.assertIsNone(self.client.get("/api/comparison?template_id=dean").json()["rows"][0]["total"])
        self.assertEqual(self.client.post(self.url + "/score", json={"revision": old["revision"], "rubric_hash": old["rubric_hash"]}).status_code, 409)

    def test_comparison_distinguishes_zero_from_missing_scores(self):
        self.upload()
        self.analyze()
        self.select("dean")
        self.save_fake_score(0)
        other_id = self.client.post("/api/profiles", json={"name": "No score"}).json()["id"]
        rows = {r["id"]: r for r in self.client.get("/api/comparison?template_id=dean").json()["rows"]}
        self.assertEqual(rows[self.job_id]["total"], 0)
        self.assertEqual(rows[self.job_id]["status"], "Scored")
        self.assertIsNone(rows[other_id]["total"])

    def test_busy_scoring_blocks_template_selection_and_edit(self):
        self.upload()
        self.analyze()
        self.select("dean")
        main.update_job(self.job_id, score_status="analyzing")
        self.assertEqual(self.select("ustf").status_code, 409)
        self.assertEqual(self.client.put("/api/templates/dean", json=RUBRIC.model_dump()).status_code, 409)
        self.assertEqual(self.client.put("/api/templates/ustf", json=RUBRIC.model_dump()).status_code, 200)

    def test_history_captures_input_output_usage_without_credentials(self):
        self.upload()
        response = httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"content": PROFILE.model_dump_json()}, "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 1234, "completion_tokens": 456}})
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.post.return_value = response
        with patch.object(ai, "read_api_key", return_value="secret-test-key"), patch.object(ai.httpx, "AsyncClient", return_value=upstream):
            self.run_action()
            self.assertEqual(self.wait_job()["status"], "completed")
        row = self.client.get("/api/requests").json()[0]
        self.assertEqual((row["input_tokens"], row["output_tokens"]), (1234, 456))
        detail = self.client.get(f"/api/requests/{self.job_id}/{row['id']}").json()
        self.assertIn("Alex Example", detail["input"]["messages"][1]["content"])
        self.assertEqual(detail["output"]["content"], PROFILE.model_dump_json())
        self.assertNotIn("secret-test-key", json.dumps(detail))
        self.assertNotIn("Authorization", json.dumps(detail))
        self.assertEqual(detail["input"], upstream.post.call_args.kwargs["json"])
        self.assertEqual(self.client.get(f"/api/requests/{self.job_id}/unknown").status_code, 404)
        self.client.__exit__(None, None, None)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.assertEqual(self.client.get(f"/api/requests/{self.job_id}/{row['id']}").json()["output"], detail["output"])

    def test_history_keeps_invalid_output_and_separate_retry(self):
        self.upload()
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.post.return_value = httpx.Response(200, json={"choices": [{"message": {"content": "{broken json"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 10}})
        with patch.object(ai, "read_api_key", return_value="test"), patch.object(ai.httpx, "AsyncClient", return_value=upstream):
            self.run_action()
            self.assertEqual(self.wait_job()["status"], "failed")
        first = self.client.get("/api/requests").json()[0]
        detail = self.client.get(f"/api/requests/{self.job_id}/{first['id']}").json()
        self.assertEqual(detail["output"]["content"], "{broken json")
        self.assertIn("validation", detail["error"])
        self.analyze()
        history = self.client.get("/api/requests").json()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "completed")
        self.assertEqual(history[1]["status"], "failed")

    def test_history_aggregates_candidates_and_old_requests_without_content(self):
        self.upload()
        self.analyze()
        other = self.client.post("/api/profiles", json={"name": "Other candidate"}).json()
        main.update_job(other["id"], runs=[{"id": "a" * 32, "operation": "score", "status": "failed", "started_at": main.now(), "usage": None}])
        rows = self.client.get("/api/requests").json()
        self.assertEqual({row["candidate_id"] for row in rows}, {self.job_id, other["id"]})
        detail = self.client.get(f"/api/requests/{other['id']}/{'a' * 32}").json()
        self.assertIsNone(detail["input"])
        self.assertIsNone(detail["output"])
        self.assertIsNone(detail["cost_usd"])

    def test_legacy_migration_keeps_original_and_source_number_gaps(self):
        path = main.folder(self.job_id)
        content = word_bytes()
        (path / "original.docx").write_bytes(content)
        main.write_json(path / "text.json", [{"number": 1, "text": "Alex Example"}, {"number": 3, "text": "PhD, Example University"}])
        profile = PROFILE.model_copy(deep=True)
        profile.bio.source_pages = [3]
        main.write_json(path / "profile.json", profile.model_dump())
        main.write_json(path / "job.json", {"id": self.job_id, "filename": "old.docx", "extension": ".docx", "status": "completed", "name": "Alex", "created_at": main.now()})
        main.migrate_legacy(path)
        job = self.client.get(self.url).json()
        self.assertEqual(job["profile"]["bio"]["source_pages"], [3])
        self.assertEqual([s["number"] for s in job["sources"]], [1, 3])
        self.assertEqual((path / "original.docx").read_bytes(), content)
        main.migrate_legacy(path)
        self.assertEqual(len(self.client.get(self.url).json()["documents"]), 1)


class ModelTests(unittest.IsolatedAsyncioTestCase):
    def pages(self):
        return [{"number": 1, "text": "Alex Example"}, {"number": 2, "text": "Education | PhD, Example University, 2020"}]

    async def response(self, content, action="profile", finish_reason="stop"):
        response = httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                                            "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch.object(ai, "read_api_key", return_value="test-key"), patch.object(ai.httpx, "AsyncClient", return_value=client):
            try:
                if action == "profile":
                    return await ai.generate_profile(self.pages())
                return await ai.generate_score(self.pages(), PROFILE.model_dump(), RUBRIC)
            finally:
                client.post.assert_awaited_once()
                self.assertEqual(client.post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})

    async def test_single_request_valid_profile(self):
        profile, receipt = await self.response(PROFILE.model_dump_json())
        self.assertEqual(profile.bio.name, "Alex Example")
        self.assertEqual(receipt["input_tokens"], 100)

    async def test_invalid_json_never_triggers_a_second_request(self):
        with self.assertRaises(AIError) as context:
            await self.response("{invalid")
        self.assertEqual(context.exception.usage["output_tokens"], 50)

    async def test_invalid_source_never_triggers_repair(self):
        profile = PROFILE.model_copy(deep=True)
        profile.bio.source_pages = [999]
        with self.assertRaises(AIError):
            await self.response(profile.model_dump_json())

    async def test_truncation_is_explicit(self):
        with self.assertRaisesRegex(AIError, "16,000-token limit") as context:
            await self.response(PROFILE.model_dump_json(), finish_reason="length")
        self.assertEqual(context.exception.usage["output_tokens"], 50)

    def test_scoring_uses_original_evidence_without_duplicate_generated_profile(self):
        profile = PROFILE.model_dump()
        profile["bio"]["summary"] = "GENERATED_PROFILE_SENTINEL"
        messages = ai.score_messages(self.pages(), profile, RUBRIC)
        self.assertNotIn("GENERATED_PROFILE_SENTINEL", messages[1]["content"])
        self.assertIn("PhD, Example University, 2020", messages[1]["content"])
        self.assertIn(RUBRIC.model_dump_json(), messages[1]["content"])

    async def test_overlong_score_explanation_is_rejected_without_retry(self):
        value = copy.deepcopy(SCORE)
        value["sections"][0]["rationale"] = "Reconsidering the same points. " * 30
        with self.assertRaisesRegex(AIError, "sections.0.rationale"):
            await self.response(json.dumps(value), action="score")

    async def test_non_length_stop_has_distinct_error(self):
        with self.assertRaisesRegex(AIError, "stopped before completing"):
            await self.response("", finish_reason="content_filter")

    async def test_score_verifies_quotes_and_calculates_total(self):
        result, _ = await self.response(json.dumps(SCORE), action="score")
        self.assertEqual(result["total"], 30)
        self.assertEqual(result["max_score"], 30)

    def test_score_accepts_pdf_wraps_and_punctuation_spacing(self):
        examples = [
            ("Hetero-\ngeneous Formations.”IEEE", "Heterogeneous Formations.” IEEE"),
            ("Artificial Intelli-\r\n gence, 2024.", "Artificial Intelligence, 2024."),
            ("Deep Rein-\nforcement Learning", "Deep Reinforcement Learning"),
            ("including multi -robot systems and vision -based autonomy", "including multi-robot systems and vision-based autonomy"),
            ("Chen †,Xiao ∗. Research", "Chen †, Xiao ∗. Research"),
            ("co-\nsupervised four students", "co-supervised four students"),
        ]
        for source, quote in examples:
            value = copy.deepcopy(SCORE)
            value["sections"][0]["evidence"][0]["quote"] = quote
            with self.subTest(source=source):
                result = ai.validate_scores(ScoreResponse.model_validate(value), [{"number": 2, "text": source}], RUBRIC)
                self.assertEqual(result["total"], 30)

    def test_pdf_normalization_does_not_accept_changed_facts_or_stitched_quotes(self):
        examples = [
            ("Mentored 4 PhD students", "Mentored 40 PhD students"),
            ("PhD not completed", "PhD completed"),
            ("PhD expected in 2027", "PhD completed in 2027"),
            ("GPA: 4.6/5.0", "GPA: 46/5.0"),
            ("Research in multi-robot systems", "Research in multirobot systems"),
            ("Undergrad course A. • Undergrad course B.", "Undergrad course A. Undergrad course B."),
            ("Education elsewhere. Then PhD", "Education PhD"),
        ]
        for source, quote in examples:
            with self.subTest(source=source):
                self.assertFalse(ai.quote_in_source(quote, source))

    async def test_score_error_identifies_evidence_and_preserves_usage(self):
        value = copy.deepcopy(SCORE)
        value["sections"][0]["evidence"][0]["quote"] = "Invented qualification"
        with self.assertRaisesRegex(AIError, "Education, evidence 1: quote does not match source 2") as context:
            await self.response(json.dumps(value), action="score")
        self.assertEqual(context.exception.usage["output_tokens"], 50)

    async def test_score_rejects_hallucination_excess_points_and_duplicate_sections(self):
        for mutation in ["quote", "points", "duplicate", "missing", "no_evidence"]:
            value = copy.deepcopy(SCORE)
            if mutation == "quote": value["sections"][0]["evidence"][0]["quote"] = "Invented qualification"
            if mutation == "points": value["sections"][0]["score"] = 100
            if mutation == "duplicate": value["sections"].append(copy.deepcopy(value["sections"][0]))
            if mutation == "missing": value["sections"] = []
            if mutation == "no_evidence": value["sections"][0]["evidence"] = []
            with self.subTest(mutation=mutation), self.assertRaises(AIError):
                await self.response(json.dumps(value), action="score")

    async def test_timeout_makes_one_request(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = httpx.ReadTimeout("timeout")
        with patch.object(ai, "read_api_key", return_value="test-key"), patch.object(ai.httpx, "AsyncClient", return_value=client):
            with self.assertRaisesRegex(AIError, "No automatic retry"):
                await ai.generate_profile(self.pages())
        client.post.assert_awaited_once()

    def test_rubric_rejects_duplicate_ids_nan_and_negative_maximum(self):
        for mutation in ["duplicate", "nan", "negative"]:
            value = RUBRIC.model_dump()
            if mutation == "duplicate": value["sections"] *= 2
            if mutation == "nan": value["sections"][0]["max_score"] = float('nan')
            if mutation == "negative": value["sections"][0]["max_score"] = -1
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                Rubric.model_validate(value)

    def test_estimate_includes_schema_and_unknown_model_has_no_fabricated_price(self):
        estimate = ai.estimate(ai.profile_messages(self.pages()), "analysis")
        self.assertGreater(estimate["input_tokens"], 1000)
        with patch.object(ai, "MODEL", "unknown-model"), patch.dict(ai.os.environ, {}, clear=True):
            self.assertIsNone(ai.estimate(ai.profile_messages(self.pages()), "analysis")["estimated_usd"])

    def test_usage_prices_cache_hits_separately(self):
        with patch.object(ai, "pricing", return_value={"input": 1, "cached": .1, "output": 2}):
            receipt = ai.usage_receipt({"usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 500, "completion_tokens": 100}})
            self.assertAlmostEqual(receipt["cost_usd"], .00075)
            self.assertIsNone(ai.usage_receipt({})["cost_usd"])


if __name__ == "__main__":
    unittest.main()
