import asyncio
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import ai, interview_analysis as analysis, interview_models, interviews, main, questions
from backend.schema import Rubric


class InterviewAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job_id, self.interview_id = "a" * 32, "b" * 32
        self.candidate = self.root / self.job_id
        self.path = self.candidate / "interviews" / self.interview_id
        self.path.mkdir(parents=True)
        self.job = {"id": self.job_id, "revision": 1, "status": "completed", "score_status": "none"}
        interviews.write_json(self.candidate / "job.json", self.job)
        interviews.write_json(self.path / "state.json", {"id": self.interview_id, "status": "completed", "stage": "completed"})
        self.profile = {"bio": {"name": "Candidate"}}
        rubric = Rubric(title="Lecturer", sections=[{"id": "teaching", "title": "Teaching", "max_score": 100, "rules": "Review teaching evidence."}])
        patches = [patch.object(main, "STORAGE", self.root), patch.object(main, "LOCKS", {}),
                   patch.object(main, "LIMIT", asyncio.Semaphore(2)),
                   patch.object(main, "current_profile", side_effect=lambda job: copy.deepcopy(self.profile)),
                   patch.object(main, "sources", return_value=[]), patch.object(main, "rubric_for", return_value=rubric),
                   patch.object(ai, "read_api_key", return_value="test-key")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.rows = [
            {"id": "approved-1", "factor": "teaching", "text": "Which course did you teach, and for how long?",
             "clarification": "Teaching details.", "criteria": ["Name a course.", "State the duration."], "approved": True,
             "sources": [], "evidence": "", "evidence_refs": [], "origin": "manual"},
            {"id": "draft-2", "factor": "research", "text": "Which research topic interests you?",
             "clarification": "Research details.", "criteria": ["Name a topic."], "approved": False,
             "sources": [], "evidence": "", "evidence_refs": [], "origin": "manual"}]
        _, context_hash = questions.context(self.job_id)
        questions.write_state(self.job_id, {**questions.empty_state(), "status": "completed", "version": 1,
                                           "context_hash": context_hash, "questions": copy.deepcopy(self.rows)})
        self.segments = [
            {"id": "s1", "start": 0, "end": 1, "text": "Tell us about your work.", "speaker": "candidate", "uncertain": False},
            {"id": "s2", "start": 1, "end": 6, "text": "I taught robotics for three years.", "speaker": "interviewer", "uncertain": False},
            {"id": "s3", "start": 7, "end": 9, "text": "How large is your school?", "speaker": "candidate", "uncertain": False},
            {"id": "s4", "start": 10, "end": 12, "text": "We have one hundred faculty members.", "speaker": "candidate", "uncertain": False},
            {"id": "s5", "start": 14, "end": 16, "text": "She has left. I think this candidate taught well.", "speaker": "candidate", "uncertain": False}]
        interviews.write_json(self.path / "transcript.json", {"segments": self.segments})
        interviews.write_json(self.path / "qa.json", {"question_snapshot": {"questions": [{"id": "old-question", "text": "Old question."}]}, "qa": [
            {"id": "qa1", "question_segment_ids": ["s1"], "answer_segment_ids": ["s2"]},
            {"id": "qa2", "question_segment_ids": ["s3"], "answer_segment_ids": ["s4"]}]})
        self.review = {"transcript_sha256": hashlib.sha256((self.path / "transcript.json").read_bytes()).hexdigest(),
                       "qa_sha256": hashlib.sha256((self.path / "qa.json").read_bytes()).hexdigest(),
                       "qa_directions": {"qa1": "panel_to_candidate", "qa2": "candidate_to_panel"},
                       "panel_only_from_segment_id": "s5"}
        interviews.write_json(self.path / "role_review.json", self.review)
        self.mock_call = AsyncMock(side_effect=self.model_response)
        mocked = patch.object(ai, "call_json", self.mock_call)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.qwen = patch.object(interview_models, "transcribe_chunk", AsyncMock())
        self.mock_qwen = self.qwen.start()
        self.addCleanup(self.qwen.stop)
        app = FastAPI()
        app.include_router(analysis.router)
        app.include_router(questions.router)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(analysis.TASKS.clear)
        self.base = f"/api/profiles/{self.job_id}/interviews/{self.interview_id}/question-analysis"

    def model_response(self, messages):
        payload = json.loads(messages[1]["content"])
        results = []
        for question in payload["CURRENT_APPROVED_QUESTIONS"]:
            results.append({"question_id": question["id"], "analysis": "The candidate names the course and duration.",
                            "evidence": [{"segment_id": "s2", "role": "candidate", "role_reason": "A first-person response to the panel's question."}],
                            "criteria": [{"index": index, "status": "met", "analysis": "Directly stated by the candidate.", "segment_ids": ["s2"]}
                                         for index in range(len(question["criteria"]))]})
        return json.dumps({"results": results}), {"model": "mock", "cost_usd": .01}

    def state(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait(self):
        for _ in range(200):
            state = self.state()
            if state["status"] != "analyzing":
                return state
            time.sleep(.01)
        self.fail("Analysis did not finish")

    def analyze(self, retry=False):
        current = self.state()
        response = self.client.post(self.base, json={"context_hash": current["current_context_hash"], "retry": retry})
        self.assertIn(response.status_code, (200, 202), response.text)
        return self.wait()

    def change_questions(self, mutate):
        state = questions.read_state(self.job_id)
        mutate(state["questions"])
        state["version"] += 1
        questions.write_state(self.job_id, state)

    def test_get_estimate_no_calls_and_approved_full_transcript_context_only(self):
        self.assertEqual(self.state()["approved_count"], 1)
        estimate = self.client.get(self.base + "/estimate")
        self.assertEqual(estimate.status_code, 200)
        self.assertEqual(estimate.json()["calls"], 1)
        self.mock_call.assert_not_called()
        self.mock_qwen.assert_not_called()
        self.assertFalse((self.path / "question-analysis").exists())
        snapshot, _, _, error = analysis.current_context(self.job_id, self.interview_id)
        self.assertIsNone(error)
        sent = json.loads(analysis.messages(snapshot)[1]["content"])
        self.assertEqual([q["id"] for q in sent["CURRENT_APPROVED_QUESTIONS"]], ["approved-1"])
        self.assertEqual(len(sent["FULL_TRANSCRIPT"]), 5)
        self.assertTrue(all("speaker" not in segment for segment in sent["FULL_TRANSCRIPT"]))
        self.assertEqual(sent["EXCLUDED_PANEL_SEGMENT_IDS"], ["s1", "s4", "s5"])
        self.assertEqual(sent["REVIEWED_CANDIDATE_SEGMENT_IDS"], ["s2", "s3"])

    def test_persisted_exact_evidence_and_completed_cache_never_recall_models(self):
        result = self.analyze()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["stale"])
        citation = result["results"][0]["evidence"][0]
        self.assertEqual((citation["text"], citation["start"], citation["end"]), (self.segments[1]["text"], 1, 6))
        self.assertEqual(result["results"][0]["criteria"][0]["evidence"], [citation])
        run = self.path / "question-analysis" / "runs" / result["run_id"]
        for filename in ("context.json", "messages.json", "response.txt", "receipt.json", "result.json"):
            self.assertTrue((run / filename).is_file(), filename)
        response = self.client.post(self.base, json={"context_hash": result["current_context_hash"]})
        self.assertEqual(response.status_code, 200)
        for _ in range(3):
            self.state()
        estimate = self.client.get(self.base + "/estimate").json()
        self.assertTrue(estimate["cached"])
        self.assertEqual(estimate["calls"], 0)
        self.mock_call.assert_awaited_once()
        self.mock_qwen.assert_not_called()

    def test_approval_and_criteria_changes_stale_results_preserve_history(self):
        initial = self.analyze()
        self.change_questions(lambda rows: rows[1].update(text="Changed draft question?"))
        self.assertFalse(self.state()["stale"])
        self.change_questions(lambda rows: rows[0].update(approved=False))
        current = self.state()
        self.assertTrue(current["stale"])
        self.assertEqual(current["approved_count"], 0)
        self.assertFalse(current["can_analyze"])
        self.assertEqual(self.client.get(self.base + "/estimate").status_code, 409)
        self.change_questions(lambda rows: rows[0].update(approved=True, criteria=["Describe the course and duration."]))
        self.assertTrue(self.state()["stale"])
        updated = self.analyze()
        self.assertEqual(updated["status"], "completed")
        self.assertNotEqual(updated["run_id"], initial["run_id"])
        self.assertTrue((self.path / "question-analysis/runs" / initial["run_id"] / "result.json").is_file())
        self.assertEqual(self.mock_call.await_count, 2)

    def test_transcript_edit_invalidates_review_and_dossier_edit_blocks_analysis(self):
        self.analyze()
        self.segments[1]["text"] = "Changed source speech."
        interviews.write_json(self.path / "transcript.json", {"segments": self.segments})
        current = self.state()
        self.assertTrue(current["stale"])
        self.assertFalse(current["can_analyze"])
        self.assertIn("speaker review is stale", current["context_error"])
        self.profile["bio"]["name"] = "Changed dossier"
        self.assertFalse(self.state()["can_analyze"])
        self.mock_call.assert_awaited_once()

    def test_unknown_or_panel_evidence_failed_request_retains_old_result_and_requires_retry(self):
        initial = self.analyze()
        self.change_questions(lambda rows: rows[0].update(criteria=["Name a course."]))
        valid_messages = analysis.messages(analysis.current_context(self.job_id, self.interview_id)[0])
        invalid = json.loads(self.model_response(valid_messages)[0])
        invalid["results"][0]["evidence"][0]["segment_id"] = "s4"
        invalid["results"][0]["criteria"][0]["segment_ids"] = ["s4"]
        self.mock_call.side_effect = None
        self.mock_call.return_value = json.dumps(invalid), {"cost_usd": .02}
        failed = self.analyze()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["results"], initial["results"])
        self.assertTrue(failed["stale"])
        run = self.path / "question-analysis/runs" / failed["run_id"]
        self.assertTrue((run / "response.txt").exists())
        self.assertEqual(interviews.read_json(run / "failure.json")["usage"]["cost_usd"], .02)
        response = self.client.post(self.base, json={"context_hash": failed["current_context_hash"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.mock_call.await_count, 2)
        self.mock_call.side_effect = self.model_response
        recovered = self.analyze(retry=True)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(self.mock_call.await_count, 3)

    def test_busy_blocks_second_call_and_question_edits(self):
        async def slow(messages):
            await asyncio.sleep(.2)
            return self.model_response(messages)
        self.mock_call.side_effect = slow
        payload = {"context_hash": self.state()["current_context_hash"]}
        self.assertEqual(self.client.post(self.base, json=payload).status_code, 202)
        self.assertTrue(analysis.is_busy(self.job_id))
        self.assertEqual(self.client.post(self.base, json=payload).status_code, 409)
        qstate = questions.read_state(self.job_id)
        edit = self.client.put(f"/api/profiles/{self.job_id}/questions", json={"version": qstate["version"], "questions": qstate["questions"]})
        self.assertEqual(edit.status_code, 409)
        self.assertEqual(self.wait()["status"], "completed")
        self.mock_call.assert_awaited_once()

    def test_old_context_or_unapproved_questions_cannot_start(self):
        payload = {"context_hash": self.state()["current_context_hash"]}
        self.change_questions(lambda rows: rows[0].update(criteria=["Changed criterion."]))
        self.assertEqual(self.client.post(self.base, json=payload).status_code, 409)
        self.change_questions(lambda rows: rows[0].update(approved=False))
        payload = {"context_hash": self.state()["current_context_hash"]}
        self.assertEqual(self.client.post(self.base, json=payload).status_code, 409)
        self.mock_call.assert_not_called()

    def test_restart_marks_interrupted_without_model_calls(self):
        snapshot, context_hash, _, _ = analysis.current_context(self.job_id, self.interview_id)
        state = {**analysis.empty_state(), "status": "analyzing", "context_hash": context_hash, "run_id": "c" * 32}
        run = self.path / "question-analysis/runs" / state["run_id"]
        run.mkdir(parents=True)
        interviews.write_json(run / "context.json", snapshot)
        analysis.write_state(self.job_id, self.interview_id, state)
        asyncio.run(analysis.startup())
        current = self.state()
        self.assertEqual(current["status"], "interrupted")
        self.assertFalse(analysis.is_busy(self.job_id))
        self.assertEqual(self.client.post(self.base, json={"context_hash": context_hash}).status_code, 409)
        self.mock_call.assert_not_called()

    def test_validation_rejects_unknown_ids_missing_questions_and_unsupported_criteria(self):
        snapshot, _, _, _ = analysis.current_context(self.job_id, self.interview_id)
        valid = json.loads(self.model_response(analysis.messages(snapshot))[0])
        variants = []
        for field, value in [("question_id", "old-question")]:
            changed = copy.deepcopy(valid)
            changed["results"][0][field] = value
            variants.append(changed)
        for sid, role in [("invented", "candidate"), ("s4", "candidate"), ("s5", "candidate"), ("s2", "panel"), ("s2", "uncertain")]:
            changed = copy.deepcopy(valid)
            changed["results"][0]["evidence"][0].update(segment_id=sid, role=role)
            for criterion in changed["results"][0]["criteria"]:
                criterion["segment_ids"] = [sid]
            variants.append(changed)
        changed = copy.deepcopy(valid)
        changed["results"][0]["criteria"].pop()
        variants.append(changed)
        changed = copy.deepcopy(valid)
        changed["results"][0]["criteria"][1].update(status="not_addressed")
        variants.append(changed)
        changed = copy.deepcopy(valid)
        changed["results"][0]["criteria"][1].update(segment_ids=[])
        variants.append(changed)
        changed = copy.deepcopy(valid)
        changed["results"][0]["criteria"][1].update(segment_ids=["s3"])
        variants.append(changed)
        changed = copy.deepcopy(valid)
        changed["results"][0]["criteria"][1].update(segment_ids=["s2", "s2"])
        variants.append(changed)
        for value in variants:
            with self.assertRaises(ValueError):
                analysis.validate_result(json.dumps(value), snapshot)
        accepted = analysis.validate_result(json.dumps(valid), snapshot)
        self.assertEqual(accepted[0]["evidence"][0]["text"], self.segments[1]["text"])

    def test_overall_status_is_derived_from_supported_criteria_not_model_status(self):
        snapshot, _, _, _ = analysis.current_context(self.job_id, self.interview_id)
        valid = json.loads(self.model_response(analysis.messages(snapshot))[0])
        for statuses, expected in [(["met", "met"], "found"), (["met", "partial"], "partial"),
                                   (["partial", "not_addressed"], "partial"),
                                   (["met", "not_addressed"], "partial"),
                                   (["not_addressed", "not_addressed"], "not_found")]:
            for model_status in (None, "found", "partial", "not_found"):
                with self.subTest(criteria=statuses, model_status=model_status):
                    payload = copy.deepcopy(valid)
                    row = payload["results"][0]
                    if model_status is not None:
                        row["status"] = model_status
                    for criterion, status in zip(row["criteria"], statuses):
                        criterion.update(status=status, segment_ids=[] if status == "not_addressed" else ["s2"])
                    result = analysis.validate_result(json.dumps(payload), snapshot)[0]
                    self.assertEqual(result["status"], expected)
                    self.assertEqual([criterion["status"] for criterion in result["criteria"]], statuses)
                    self.assertEqual(result["analysis"], row["analysis"])
                    self.assertEqual(len(result["evidence"]), 0 if expected == "not_found" else 1)

    def test_conflicting_model_status_completes_without_retry(self):
        def response(messages):
            content, usage = self.model_response(messages)
            payload = json.loads(content)
            payload["results"][0]["status"] = "found"
            payload["results"][0]["criteria"][1]["status"] = "partial"
            return json.dumps(payload), usage
        self.mock_call.side_effect = response
        result = self.analyze()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"][0]["status"], "partial")
        self.assertEqual(result["results"][0]["evidence"][0]["text"], self.segments[1]["text"])
        self.mock_call.assert_awaited_once()
        self.mock_qwen.assert_not_called()

    def save_failed_response(self):
        snapshot, context_hash, _, _ = analysis.current_context(self.job_id, self.interview_id)
        content, usage = self.model_response(analysis.messages(snapshot))
        payload = json.loads(content)
        payload["results"][0]["status"] = "found"
        payload["results"][0]["criteria"][1]["status"] = "partial"
        state = {**analysis.empty_state(), "status": "failed", "context_hash": context_hash,
                 "run_id": "c" * 32, "error": "A found answer must meet every criterion with candidate evidence.",
                 "attempt_usage": usage}
        run = self.path / "question-analysis/runs" / state["run_id"]
        run.mkdir(parents=True)
        interviews.write_json(run / "context.json", snapshot)
        (run / "response.txt").write_text(json.dumps(payload), encoding="utf-8")
        interviews.write_json(run / "receipt.json", usage)
        interviews.write_json(run / "failure.json", {"error": state["error"], "usage": usage})
        analysis.write_state(self.job_id, self.interview_id, state)
        return run, state

    def test_explicit_retry_recovers_saved_response_without_credentials_or_model_calls(self):
        run, failed = self.save_failed_response()
        originals = {name: (run / name).read_bytes() for name in ("context.json", "response.txt", "receipt.json", "failure.json")}
        estimate = self.client.get(self.base + "/estimate").json()
        self.assertTrue(estimate["cached"])
        self.assertEqual(estimate["calls"], 0)
        self.assertEqual(estimate["estimated_usd"], 0)
        self.assertEqual(self.state()["status"], "failed")
        self.assertFalse((run / "result.json").exists())
        self.assertEqual(self.client.post(self.base, json={"context_hash": estimate["context_hash"]}).status_code, 409)
        with patch.object(ai, "read_api_key", side_effect=AssertionError("Recovery must not need API access")):
            response = self.client.post(self.base, json={"context_hash": estimate["context_hash"], "retry": True})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["error"])
        self.assertFalse(result["stale"])
        self.assertTrue(result["recovered_from_saved_response"])
        self.assertEqual(result["run_id"], failed["run_id"])
        self.assertEqual(result["usage"], failed["attempt_usage"])
        self.assertEqual(result["results"][0]["status"], "partial")
        self.assertEqual(interviews.read_json(run / "result.json")["results"], result["results"])
        for name, content in originals.items():
            self.assertEqual((run / name).read_bytes(), content, name)
        self.assertEqual(self.analyze()["run_id"], failed["run_id"])
        self.mock_call.assert_not_called()
        self.mock_qwen.assert_not_called()

    def test_recovery_rejects_changed_context_unknown_sources_and_panel_evidence(self):
        run, state = self.save_failed_response()
        snapshot, context_hash, _, _ = analysis.current_context(self.job_id, self.interview_id)
        content = (run / "response.txt").read_text(encoding="utf-8")
        original_context = (run / "context.json").read_bytes()
        changed_snapshot = copy.deepcopy(snapshot)
        changed_snapshot["questions"][0]["criteria"] = ["Changed criterion."]
        interviews.write_json(run / "context.json", changed_snapshot)
        self.assertIsNone(analysis.recoverable_result(self.job_id, self.interview_id, state, snapshot, context_hash))
        (run / "context.json").write_bytes(original_context)
        for sid in ("invented", "s4", "s5"):
            payload = json.loads(content)
            payload["results"][0]["evidence"][0]["segment_id"] = sid
            for criterion in payload["results"][0]["criteria"]:
                criterion["segment_ids"] = [sid]
            (run / "response.txt").write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(analysis.recoverable_result(self.job_id, self.interview_id, state, snapshot, context_hash))
            self.assertFalse(self.client.get(self.base + "/estimate").json()["cached"])
        (run / "response.txt").write_text(content, encoding="utf-8")
        self.change_questions(lambda rows: rows[0].update(criteria=["Changed criterion."]))
        current_snapshot, current_hash, _, _ = analysis.current_context(self.job_id, self.interview_id)
        self.assertIsNone(analysis.recoverable_result(self.job_id, self.interview_id, state, current_snapshot, current_hash))
        self.assertEqual(self.client.post(self.base, json={"context_hash": context_hash, "retry": True}).status_code, 409)
        self.assertEqual(self.state()["status"], "failed")
        self.mock_call.assert_not_called()
        self.mock_qwen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
