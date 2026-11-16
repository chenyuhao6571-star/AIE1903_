import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import ai, main, questions
from backend.questions_prompt import FACTORS
from backend.schema import Rubric


SOURCE = "The candidate designed a robotics course and assessed student projects."


def response_content():
    factors = []
    rows = []
    for factor in FACTORS:
        factors.append({"id": factor["id"], "dimensions": [
            {"id": key, "supported": i == 0, "detail": title,
             "evidence": [{"source_id": 1, "quote": SOURCE}] if i == 0 else []}
            for i, (key, title) in enumerate(factor["dimensions"])]})
        rows.append({"factor": factor["id"], "clarification": "The duration needs clarification.",
                     "text": f"Please describe the duration of your {factor['title']} activity.",
                     "criteria": ["The duration is stated.", "A concrete example is given."],
                     "evidence_refs": [{"source_id": 1, "quote": SOURCE}]})
    return json.dumps({"factors": factors, "questions": rows})


class QuestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job_id = "a" * 32
        self.folder = self.root / self.job_id
        self.folder.mkdir()
        self.job = {"id": self.job_id, "revision": 1, "status": "completed", "score_status": "none"}
        (self.folder / "job.json").write_text(json.dumps(self.job))
        self.profile = {"bio": {"name": "Candidate"}, "teaching": [{"title": "Course", "description": SOURCE}]}
        self.pages = [{"number": 1, "filename": "CV.pdf", "page_number": 1, "unit": "page", "text": SOURCE}]
        self.rubric = Rubric(title="Lecturer", sections=[{"id": "teaching", "title": "Teaching", "max_score": 100, "rules": "Review documented teaching experience."}])
        self.mock_call = AsyncMock(return_value=(response_content(), {"model": "test", "cost_usd": .01}))
        patches = [patch.object(main, "STORAGE", self.root), patch.object(main, "LOCKS", {}),
                   patch.object(main, "LIMIT", asyncio.Semaphore(2)),
                   patch.object(main, "current_profile", side_effect=lambda job: copy.deepcopy(self.profile)),
                   patch.object(main, "sources", side_effect=lambda job: copy.deepcopy(self.pages)),
                   patch.object(main, "rubric_for", side_effect=lambda job_id: self.rubric),
                   patch.object(ai, "call_json", self.mock_call)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        app = FastAPI()
        app.include_router(questions.router)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.base = f"/api/profiles/{self.job_id}/questions"

    def state(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def generate(self, replace=False):
        estimate = self.client.get(self.base + "/estimate").json()
        response = self.client.post(self.base + "/generate", json={"version": estimate["version"], "context_hash": estimate["context_hash"], "replace": replace})
        self.assertIn(response.status_code, (200, 202), response.text)
        for _ in range(300):
            result = self.state()
            if result["status"] != "generating":
                return result
            time.sleep(.01)
        self.fail("Generation did not complete")

    def save(self, state, rows):
        response = self.client.put(self.base, json={"version": state["version"], "questions": rows})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_get_and_estimate_are_read_only_then_generation_persists(self):
        self.assertEqual(self.state()["questions"], [])
        self.client.get(self.base + "/estimate")
        self.mock_call.assert_not_called()
        generated = self.generate()
        self.assertEqual(generated["status"], "completed")
        self.assertEqual([f["coverage"] for f in generated["factors"]], [25] * 5)
        self.assertEqual(len(generated["questions"]), 5)
        self.assertEqual(len(questions.current_snapshot(self.job_id)["questions"]), 5)
        self.assertEqual(questions.approved_snapshot(self.job_id)["questions"], [])
        self.assertTrue((self.folder / "questions" / "state.json").is_file())
        for _ in range(3):
            self.assertEqual(self.state()["questions"], generated["questions"])
        self.mock_call.assert_awaited_once()

    def test_same_completed_context_is_cached_without_explicit_replace(self):
        before = self.generate()
        after = self.generate()
        self.assertEqual(before["version"], after["version"])
        self.mock_call.assert_awaited_once()

    def test_approval_edit_and_undo_preserve_sources(self):
        state = self.generate()
        rows = copy.deepcopy(state["questions"])
        rows[0]["approved"] = True
        state = self.save(state, rows)
        self.assertEqual(len(questions.approved_snapshot(self.job_id)["questions"]), 1)
        original = state["questions"][0]
        state = self.save(state, state["questions"][1:])
        state = self.save(state, [original, *state["questions"]])
        self.assertEqual(state["questions"][0]["sources"], [1])
        self.assertTrue(state["questions"][0]["approved"])
        edited = copy.deepcopy(state["questions"])
        edited[0]["criteria"] = ["Identify the course and dates."]
        state = self.save(state, edited)
        self.assertFalse(state["questions"][0]["approved"])
        self.assertEqual(questions.approved_snapshot(self.job_id)["questions"], [])

    def test_stale_context_and_version_reject_mutations(self):
        initial = self.generate()
        updated = self.save(initial, initial["questions"])
        response = self.client.put(self.base, json={"version": initial["version"], "questions": initial["questions"]})
        self.assertEqual(response.status_code, 409)
        self.profile["bio"]["name"] = "Changed profile"
        self.assertTrue(self.state()["stale"])
        self.assertEqual(questions.approved_snapshot(self.job_id)["questions"], [])
        response = self.client.put(self.base, json={"version": updated["version"], "questions": updated["questions"]})
        self.assertEqual(response.status_code, 409)

    def test_failed_generation_retains_saved_questions_and_makes_one_request(self):
        initial = self.generate()
        self.mock_call.side_effect = ai.AIError("No connection; no retry.")
        failed = self.generate(replace=True)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["questions"], initial["questions"])
        self.assertEqual(self.mock_call.await_count, 2)

    def test_rejects_hallucinated_quote_and_preserves_previous(self):
        initial = self.generate()
        content = json.loads(response_content())
        content["questions"][0]["evidence_refs"][0]["quote"] = "Invented quote which is not in the source."
        self.mock_call.return_value = json.dumps(content), {}
        failed = self.generate(replace=True)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["questions"], initial["questions"])

    def test_corrects_only_unique_exact_quote_in_same_document(self):
        pages = [*self.pages, {"number": 2, "filename": "CV.pdf", "page_number": 2, "unit": "page", "text": "Another page."}]
        content = json.loads(response_content())
        content["factors"][0]["dimensions"][0]["evidence"][0]["source_id"] = 2
        corrections = []
        factors, _ = questions.validate_result(json.dumps(content), pages, corrections)
        self.assertEqual(factors[0]["dimensions"][0]["evidence"][0]["source_id"], 1)
        self.assertEqual(corrections, [{"from_source_id": 2, "to_source_id": 1, "quote": SOURCE}])
        # Multiple occurrences or another document are not a safe reassignment.
        with self.assertRaises(ValueError):
            questions.validate_result(json.dumps(content), [*pages, {**self.pages[0], "number": 3}])
        with self.assertRaises(ValueError):
            questions.validate_result(json.dumps(content), [{**self.pages[0], "filename": "Other.pdf"}, pages[1]])

    def test_generation_blocks_duplicate_requests_and_edits(self):
        async def slow(messages):
            await asyncio.sleep(.3)
            return response_content(), {}
        self.mock_call.side_effect = slow
        estimate = self.client.get(self.base + "/estimate").json()
        payload = {"version": 0, "context_hash": estimate["context_hash"]}
        first = self.client.post(self.base + "/generate", json=payload)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.client.post(self.base + "/generate", json=payload).status_code, 409)
        self.assertEqual(self.client.put(self.base, json={"version": first.json()["version"], "questions": []}).status_code, 409)
        for _ in range(100):
            if self.state()["status"] != "generating":
                break
            time.sleep(.01)
        self.mock_call.assert_awaited_once()

    def test_old_estimate_cannot_start_request(self):
        estimate = self.client.get(self.base + "/estimate").json()
        self.profile["bio"]["name"] = "Changed"
        response = self.client.post(self.base + "/generate", json={"version": 0, "context_hash": estimate["context_hash"]})
        self.assertEqual(response.status_code, 409)
        self.mock_call.assert_not_called()

    def test_manual_questions_do_not_accept_fabricated_source_evidence(self):
        item = {"id": "manual", "factor": "teaching", "text": "Which course did you teach?", "clarification": "Course title.",
                "criteria": ["Name a course."], "approved": True, "sources": [1], "evidence": "Invented", "origin": "model"}
        state = self.save(self.state(), [item])
        self.assertEqual(state["questions"][0]["sources"], [])
        self.assertEqual(state["questions"][0]["origin"], "manual")
        self.assertFalse(state["questions"][0]["approved"])
        self.mock_call.assert_not_called()

    def test_recovery_marks_interrupted_without_relaunch(self):
        saved = questions.empty_state()
        saved.update(status="generating", version=1)
        questions.write_state(self.job_id, saved)
        asyncio.run(questions.recover_interrupted())
        self.assertEqual(self.state()["status"], "interrupted")
        self.mock_call.assert_not_called()

    def test_offline_recovery_preserves_raw_output_and_is_idempotent(self):
        self.pages.append({"number": 2, "filename": "CV.pdf", "page_number": 2, "unit": "page", "text": "Another page."})
        snapshot, _ = questions.context(self.job_id)
        content = json.loads(response_content())
        content["factors"][0]["dimensions"][0]["evidence"][0]["source_id"] = 2
        run_id = "b" * 32
        run = self.folder / "questions" / "runs" / run_id
        run.mkdir(parents=True)
        main.write_json(run / "context.json", snapshot)
        main.write_json(run / "output.json", {"content": json.dumps(content), "finish_reason": "stop"})
        raw = (run / "output.json").read_bytes()
        state = questions.empty_state()
        state.update(status="failed", run_id=run_id, version=2, usage={"cost_usd": .01})
        questions.write_state(self.job_id, state)
        recovered = asyncio.run(questions.recover_saved_result(self.job_id, run_id))
        self.assertEqual(recovered["questions"], 5)
        self.assertEqual(len(recovered["source_corrections"]), 1)
        self.assertEqual((run / "output.json").read_bytes(), raw)
        self.assertEqual(self.state()["usage"]["cost_usd"], .01)
        again = asyncio.run(questions.recover_saved_result(self.job_id, run_id))
        self.assertEqual(recovered["version"], again["version"])
        self.mock_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
