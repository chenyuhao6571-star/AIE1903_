import asyncio
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import ai, interview_media, interview_models, interviews


def wav(path, seconds=1):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000 * seconds)


def chunk_response(text, speaker="candidate"):
    return json.dumps({"segments": [{"start": 0, "end": 1, "text": text,
                                    "speaker": speaker, "uncertain": False}]})


class StoreFixture:
    def set_store(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate_id = "a" * 32
        self.candidate = self.root / self.candidate_id
        self.candidate.mkdir()
        self.locks = {}

        def folder(job_id):
            if job_id != self.candidate_id:
                raise HTTPException(404, "Candidate not found")
            return self.candidate

        fake = SimpleNamespace(STORAGE=self.root, folder=folder,
                               lock=lambda job_id: self.locks.setdefault(job_id, asyncio.Lock()),
                               read_job=lambda job_id: {"id": job_id}, ensure_idle=lambda job: None)
        self.patch = patch.object(interviews, "main_module", return_value=fake)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        interviews.TASKS.clear()
        interviews.IMPORT_LOCKS.clear()

    def record(self, status="processing"):
        interview_id = "b" * 32
        path = self.candidate / "interviews" / interview_id
        path.mkdir(parents=True)
        (path / "video.mp4").write_bytes(b"fake video")
        interviews.write_json(path / "state.json", {"id": interview_id, "candidate_id": self.candidate_id,
            "name": "interview.mp4", "size": 10, "video_file": "video.mp4", "sha256": "digest",
            "duration": 2, "status": status, "stage": "uploaded", "error": None,
            "progress": {"completed": 0, "total": 0, "percent": 0}, "created_at": interviews.now(),
            "updated_at": interviews.now(), "receipts": []})
        return interview_id, path


class InterviewAPITests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.set_store()
        app = FastAPI()
        app.include_router(interviews.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.probe = patch.object(interview_media, "probe", AsyncMock(return_value=10))
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def url(self, tail=""):
        return f"/api/profiles/{self.candidate_id}/interviews{tail}"

    def test_upload_is_persisted_and_duplicate_is_reused_without_model_call(self):
        with patch.object(interview_models, "transcribe_chunk", AsyncMock()) as transcribe:
            first = self.client.post(self.url(), files={"file": ("../my interview.mp4", b"test bytes", "video/mp4")})
            second = self.client.post(self.url(), files={"file": ("other.mp4", b"test bytes", "video/mp4")})
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.json()["id"], second.json()["id"])
            self.assertEqual(first.json()["name"], "my interview.mp4")
            self.assertEqual(len(self.client.get(self.url()).json()), 1)
            transcribe.assert_not_called()
            detail = self.client.get(self.url("/" + first.json()["id"])).json()
            self.assertEqual(detail["status"], "ready")
            self.assertEqual(detail["qa"], [])

    def test_media_range_and_path_boundary(self):
        interview_id, path = self.record("ready")
        response = self.client.get(self.url(f"/{interview_id}/video"), headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"fake")
        self.assertEqual(self.client.get(self.url("/not-valid/video")).status_code, 404)
        self.assertEqual(self.client.get(self.url(f"/{interview_id}/audio")).status_code, 404)

    def test_upload_rejects_invalid_extension_empty_file_and_oversize(self):
        self.assertEqual(self.client.post(self.url(), files={"file": ("x.txt", b"data")}).status_code, 400)
        self.assertEqual(self.client.post(self.url(), files={"file": ("x.mp4", b"")}).status_code, 400)
        with patch.object(interviews, "MAX_VIDEO_BYTES", 3):
            self.assertEqual(self.client.post(self.url(), files={"file": ("x.mp4", b"1234")}).status_code, 413)
        self.assertEqual(list((self.candidate / "interviews").glob(".upload-*")), [])

    def test_samples_are_whitelisted_and_import_does_not_process(self):
        data = self.root / "data"
        data.mkdir()
        (data / "Screening.mp4").write_bytes(b"sample")
        (data / "secret.txt").write_text("private")
        with patch.object(interviews, "SAMPLE_DIR", data):
            self.assertEqual([r["name"] for r in self.client.get("/api/interviews/samples").json()], ["Screening.mp4"])
            self.assertEqual(self.client.post(self.url("/sample"), json={"name": "../secret.txt"}).status_code, 404)
            result = self.client.post(self.url("/sample"), json={"name": "Screening.mp4"})
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json()["status"], "ready")

    def test_completed_process_returns_cache_and_failure_requires_explicit_retry(self):
        interview_id, path = self.record("completed")
        interviews.write_json(path / "qa.json", {"qa": [], "question_snapshot": {}})
        with patch.object(interview_models, "transcribe_chunk", AsyncMock()) as transcribe:
            self.assertEqual(self.client.post(self.url(f"/{interview_id}/process"), json={}).status_code, 202)
            transcribe.assert_not_called()
        interviews.update_record(path, status="failed")
        self.assertEqual(self.client.post(self.url(f"/{interview_id}/process"), json={}).status_code, 409)


class PipelineTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.set_store()
        interviews.PROCESS_LIMIT = asyncio.Semaphore(1)

    def prepare_chunks(self, path, count):
        wav(path / "audio.wav")
        (path / "chunks").mkdir()
        chunks = [{"index": index, "start": index, "duration": 1, "filename": f"{index:04d}.wav"} for index in range(count)]
        for chunk in chunks:
            (path / "chunks" / chunk["filename"]).write_bytes(b"mock audio")
        return chunks

    async def test_ten_chunks_begin_together_before_any_complete(self):
        interview_id, path = self.record()
        chunks = self.prepare_chunks(path, 10)
        started, all_started, release = set(), asyncio.Event(), asyncio.Event()

        async def transcribe(audio, duration):
            index = int(audio.stem)
            started.add(index)
            if len(started) == 10:
                all_started.set()
            await release.wait()
            return chunk_response(f"Words {index}."), {"usage": {"total_tokens": index + 1}}

        with patch.object(interview_media, "chunk_audio", return_value=chunks), \
             patch.dict(os.environ, {"QWEN_CHUNK_CONCURRENCY": "10"}), \
             patch.object(interview_models, "transcribe_chunk", side_effect=transcribe), \
             patch.object(interviews, "current_snapshot", return_value={"questions": []}), \
             patch.object(interview_models, "group_qa", AsyncMock(return_value=('{"pairs":[]}', {}))) as qa:
            task = asyncio.create_task(interviews.run_pipeline(self.candidate_id, interview_id))
            try:
                await asyncio.wait_for(all_started.wait(), 1)
                self.assertEqual(len(started), 10)
                qa.assert_not_called()
                self.assertEqual(interviews.available_segments(path), [])
                release.set()
                await asyncio.wait_for(task, 2)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        record = interviews.read_record(self.candidate_id, interview_id)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["progress"]["completed"], 10)
        self.assertEqual([s["start"] for s in interviews.available_segments(path)], list(range(10)))
        receipts = [r for r in record["receipts"] if r["stage"] == "transcription"]
        self.assertEqual({r["chunk"] for r in receipts}, set(range(10)))
        self.assertTrue(all(r["started_at"] and r["elapsed_seconds"] >= 0 for r in receipts))
        self.assertGreaterEqual(record["batch_elapsed_seconds"], 0)

    async def test_one_failed_chunk_waits_for_other_successes_and_resume_archives_only_failed(self):
        interview_id, path = self.record()
        chunks = self.prepare_chunks(path, 4)
        started, all_started, failed, release = set(), asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def transcribe(audio, duration):
            index = int(audio.stem)
            started.add(index)
            if len(started) == 4:
                all_started.set()
            await all_started.wait()
            if index == 1:
                failed.set()
                return '{"segments":[', {"usage": {"total_tokens": 4}}
            await release.wait()
            return chunk_response(f"Words {index}."), {"usage": {"total_tokens": 4}}

        with patch.object(interview_media, "chunk_audio", return_value=chunks), \
             patch.object(interview_models, "transcribe_chunk", side_effect=transcribe), \
             patch.object(interview_models, "group_qa", AsyncMock()) as qa:
            task = asyncio.create_task(interviews.run_pipeline(self.candidate_id, interview_id))
            try:
                await asyncio.wait_for(failed.wait(), 1)
                self.assertFalse(task.done())
                qa.assert_not_called()
                release.set()
                await asyncio.wait_for(task, 2)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        record = interviews.read_record(self.candidate_id, interview_id)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failed_chunk_indices"], [1])
        self.assertEqual(record["progress"]["completed"], 3)
        self.assertEqual(len(record["receipts"]), 4)
        before = {index: (path / "chunks" / f"{index:04d}.json").read_bytes() for index in (0, 2, 3)}
        with patch.object(interview_media, "chunk_audio", return_value=chunks), \
             patch.object(interview_models, "transcribe_chunk", AsyncMock(return_value=(chunk_response("Recovered."), {}))) as resume, \
             patch.object(interviews, "current_snapshot", return_value={"questions": []}), \
             patch.object(interview_models, "group_qa", AsyncMock(return_value=('{"pairs":[]}', {}))):
            await interviews.run_pipeline(self.candidate_id, interview_id)
        self.assertEqual(resume.await_count, 1)
        self.assertEqual(resume.await_args.args[0].name, "0001.wav")
        self.assertTrue(all((path / "chunks" / f"{index:04d}.json").read_bytes() == data for index, data in before.items()))
        archives = list((path / "chunks" / "attempts").glob("0001-*/0001.response.txt"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(), '{"segments":[')
        self.assertEqual(interviews.read_record(self.candidate_id, interview_id)["status"], "completed")

    async def check_stop_cancels_active_and_queued_chunks(self, shutdown):
        interview_id, path = self.record()
        chunks = self.prepare_chunks(path, 4)
        started, cancelled, two_started = set(), set(), asyncio.Event()

        async def transcribe(audio, duration):
            index = int(audio.stem)
            started.add(index)
            if len(started) == 2:
                two_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(index)
                audio.with_suffix(".partial.txt").write_text("partial")

        with patch.object(interview_media, "chunk_audio", return_value=chunks), \
             patch.dict(os.environ, {"QWEN_CHUNK_CONCURRENCY": "2"}), \
             patch.object(interview_models, "transcribe_chunk", side_effect=transcribe), \
             patch.object(interview_models, "group_qa", AsyncMock()) as qa:
            task = asyncio.create_task(interviews.run_pipeline(self.candidate_id, interview_id))
            interviews.TASKS[(self.candidate_id, interview_id)] = task
            try:
                await asyncio.wait_for(two_started.wait(), 1)
                if shutdown:
                    await asyncio.wait_for(interviews.shutdown(), 1)
                else:
                    await asyncio.wait_for(interviews.cancel_interview(self.candidate_id, interview_id), 1)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            qa.assert_not_called()
        self.assertEqual(started, {0, 1})
        self.assertEqual(cancelled, started)
        self.assertFalse(interviews.TASKS)
        self.assertEqual(len(list((path / "chunks").glob("*.partial.txt"))), 2)
        self.assertFalse(any(t.get_name().startswith("interview-chunk-") and not t.done() for t in asyncio.all_tasks()))
        self.assertEqual(interviews.read_record(self.candidate_id, interview_id)["status"], "cancelled")

    async def test_cancel_cleans_up_active_and_queued_children(self):
        await self.check_stop_cancels_active_and_queued_chunks(shutdown=False)

    async def test_shutdown_cleans_up_active_and_queued_children(self):
        await self.check_stop_cancels_active_and_queued_chunks(shutdown=True)

    async def test_resume_preserves_successful_chunks_and_qa_reconstructs_source_text(self):
        interview_id, path = self.record()
        wav(path / "audio.wav", 2)
        original_chunker = interview_media.chunk_audio
        with patch.object(interview_media, "chunk_audio", side_effect=lambda a, d: original_chunker(a, d, seconds=1)):
            transcribe = AsyncMock(side_effect=[(chunk_response("What did you teach?", "interviewer"), {"usage": {}}), ai.AIError("failed chunk")])
            with patch.object(interview_models, "transcribe_chunk", transcribe):
                await interviews.run_pipeline(self.candidate_id, interview_id)
            self.assertEqual(transcribe.await_count, 2)
            self.assertEqual(interviews.read_record(self.candidate_id, interview_id)["status"], "failed")
            self.assertEqual(len(interviews.available_segments(path)), 1)
            snapshot = {"version": 3, "questions": [{"id": "approved-1", "text": "Which course did you teach?"}]}
            grouped = json.dumps({"pairs": [{"kind": "qa", "question_segment_ids": ["c0000s0000"],
                                            "answer_segment_ids": ["c0001s0000"], "matched_question_id": "approved-1"}]})
            with patch.object(interviews, "current_snapshot", return_value=snapshot), \
                 patch.object(interview_models, "transcribe_chunk", AsyncMock(return_value=(chunk_response("I taught robotics."), {}))) as resume, \
                 patch.object(interview_models, "group_qa", AsyncMock(return_value=(grouped, {}))) as qa:
                await interviews.run_pipeline(self.candidate_id, interview_id)
            self.assertEqual(resume.await_count, 1)
            self.assertEqual(qa.await_count, 1)
            result = interviews.public_record(interviews.read_record(self.candidate_id, interview_id), path, True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["qa"][0]["question"], "What did you teach?")
            self.assertEqual(result["qa"][0]["answer"], "I taught robotics.")
            self.assertEqual(result["qa"][0]["answer_start"], 1)
            self.assertTrue(result["has_audio"] and result["has_transcript"] and result["has_qa"])

    async def test_failed_qa_does_not_repeat_transcription(self):
        interview_id, path = self.record()
        wav(path / "audio.wav")
        segment = {"id": "s1", "start": 0, "end": 1, "speaker": "candidate", "text": "My presentation.", "uncertain": False}
        interviews.write_json(path / "transcript.json", {"segments": [segment]})
        with patch.object(interviews, "current_snapshot", return_value={"questions": []}), \
             patch.object(interview_models, "transcribe_chunk", AsyncMock()) as transcribe, \
             patch.object(interview_models, "group_qa", AsyncMock(side_effect=ai.AIError("qa failure"))):
            await interviews.run_pipeline(self.candidate_id, interview_id)
            transcribe.assert_not_called()
        self.assertTrue((path / "transcript.json").exists())
        self.assertEqual(interviews.read_record(self.candidate_id, interview_id)["stage"], "qa")

    async def test_saved_chunks_resume_qa_without_overwriting_batch_timing(self):
        interview_id, path = self.record()
        chunks = self.prepare_chunks(path, 1)
        segments = interview_models.validate_transcript(chunk_response("Saved words."), chunks[0])
        interviews.write_json(path / "chunks/0000.json", {"segments": segments})
        interviews.update_record(path, batch_started_at="2026-10-27T08:06:17+00:00", batch_elapsed_seconds=38.609)
        with patch.object(interview_media, "chunk_audio", return_value=chunks), \
             patch.object(interview_models, "transcribe_chunk", AsyncMock()) as transcribe, \
             patch.object(interviews, "current_snapshot", return_value={"questions": []}), \
             patch.object(interview_models, "group_qa", AsyncMock(return_value=('{"pairs":[]}', {}))):
            await interviews.run_pipeline(self.candidate_id, interview_id)
        transcribe.assert_not_called()
        record = interviews.read_record(self.candidate_id, interview_id)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["batch_started_at"], "2026-10-27T08:06:17+00:00")
        self.assertEqual(record["batch_elapsed_seconds"], 38.609)

    async def test_restart_marks_interrupted_without_scheduling_requests(self):
        interview_id, path = self.record()
        with patch.object(interview_models, "transcribe_chunk", AsyncMock()) as transcribe:
            await interviews.startup()
            transcribe.assert_not_called()
        self.assertEqual(interviews.read_record(self.candidate_id, interview_id)["status"], "interrupted")
        self.assertFalse(interviews.TASKS)


class ModelValidationTests(unittest.TestCase):
    def test_candidate_question_keeps_full_panel_answer_across_chunks(self):
        segments = [
            {"id": "c0006s0019", "start": 1195.68, "end": 1197.28, "text": "Do you have any questions for us?", "uncertain": False},
            {"id": "c0006s0020", "start": 1198.8, "end": 1199.52, "text": "Yeah.", "uncertain": False},
            {"id": "c0006s0021", "start": 1199.52, "end": 1205.36, "text": "I know this is a new school.", "uncertain": False},
            {"id": "c0006s0022", "start": 1205.6, "end": 1216.64, "text": "How many students will you admit?", "uncertain": False},
            {"id": "c0006s0024", "start": 1219.84, "end": 1260, "text": "We admitted 149 undergraduates.", "uncertain": False},
            {"id": "c0007s0000", "start": 1260.72, "end": 1291.52, "text": "We expect more professors and students.", "uncertain": False},
        ]
        content = json.dumps({"pairs": [{"kind": "qa", "question_segment_ids": ["c0006s0021", "c0006s0022"],
                                        "answer_segment_ids": ["c0006s0024", "c0007s0000"]}]})
        grouped = interview_models.validate_qa(content, segments, {"questions": []})
        self.assertEqual(grouped["unassigned_segment_ids"], ["c0006s0019", "c0006s0020"])
        self.assertEqual(grouped["qa"][0]["question_start"], 1199.52)
        self.assertEqual(grouped["qa"][0]["answer_start"], 1219.84)
        self.assertEqual(grouped["qa"][0]["end"], 1291.52)
        self.assertEqual(grouped["qa"][0]["answer"], "We admitted 149 undergraduates. We expect more professors and students.")

    def test_missing_end_open_quote_recovery_never_changes_spoken_literals(self):
        spoken = 'The example {"start": 4, end": 5, "speaker": "candidate"} stays literal.'
        content = '{"segments":[{"start":1.25, end":2.5, "speaker":"candidate", "text":' + json.dumps(spoken) + '}]}'
        audit = []
        chunk = {"index": 0, "start": 0, "duration": 3}
        result = interview_models.validate_transcript(content, chunk, audit=audit)
        self.assertEqual(result[0]["text"], spoken)
        self.assertEqual((result[0]["start"], result[0]["end"]), (1.25, 2.5))
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["kind"], "insert_missing_end_key_open_quote")
        for broken in [content[:-1], content.replace(json.dumps(spoken), '"unfinished'), content.replace('end":2.5', 'finish":2.5')]:
            with self.assertRaises(ValueError):
                interview_models.validate_transcript(broken, chunk)

    def test_panel_discussion_remains_in_transcript_without_candidate_qa_role(self):
        segments = [
            {"id": "s1", "start": 0, "end": 1, "text": "What did you teach?", "uncertain": False},
            {"id": "s2", "start": 1, "end": 2, "text": "I taught robotics.", "uncertain": False},
            {"id": "s3", "start": 3, "end": 4, "text": "She has left. What do we think of this candidate?", "speaker": "candidate", "uncertain": False},
            {"id": "s4", "start": 4, "end": 5, "text": "The next candidate is in the waiting room.", "speaker": "candidate", "uncertain": False},
        ]
        content = json.dumps({"pairs": [{"kind": "qa", "question_segment_ids": ["s1"], "answer_segment_ids": ["s2"]}]})
        grouped = interview_models.validate_qa(content, segments, {"questions": []})
        self.assertEqual(grouped["unassigned_segment_ids"], ["s3", "s4"])
        projected = interview_models.transcript_roles(segments, grouped["qa"])
        self.assertEqual(projected[2]["text"], segments[2]["text"])
        self.assertNotIn("qa_role", projected[2])
        self.assertNotIn("qa_role", projected[3])
    def test_chunk_concurrency_is_bounded(self):
        for configured, expected in [("0", 1), ("2", 2), ("10", 10), ("999", 10), ("invalid", 10)]:
            with patch.dict(os.environ, {"QWEN_CHUNK_CONCURRENCY": configured}):
                self.assertEqual(interviews.chunk_concurrency(), expected)

    def test_missing_end_key_normalization_preserves_string_literals_and_numbers(self):
        spoken = 'Example {"start": 5, 6, "speaker": "candidate"} remains quoted.'
        row = '{"start": 1.25, 2.5, "speaker":"candidate", "text":' + json.dumps(spoken) + '}'
        content = '{"segments":[' + row + ']}'
        audit = []
        result = interview_models.validate_transcript(content, {"index": 0, "start": 0, "duration": 3}, audit=audit)
        self.assertEqual(result[0]["text"], spoken)
        self.assertEqual((result[0]["start"], result[0]["end"]), (1.25, 2.5))
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["kind"], "insert_missing_end_key")
        for broken in [content[:-1], content.replace(json.dumps(spoken), '"unfinished'), content.replace(', "text":' + json.dumps(spoken), '')]:
            with self.assertRaises(ValueError):
                interview_models.validate_transcript(broken, {"index": 0, "start": 0, "duration": 3})

    def test_qa_ignores_inverted_speaker_labels_and_preserves_text(self):
        segments = [
            {"id": "s1", "start": 0, "end": 1, "text": "Please introduce yourself.", "speaker": "candidate", "uncertain": False},
            {"id": "s2", "start": 1, "end": 2, "text": "My research focuses on software.", "speaker": "interviewer", "uncertain": False},
        ]
        messages = interview_models.qa_messages(segments, {"questions": []})
        sent = json.loads(messages[1]["content"])["TRANSCRIPT"]
        self.assertNotIn("speaker", sent[0])
        self.assertEqual(sent[1]["text"], segments[1]["text"])
        pairs = [{"id": "qa1", "kind": "qa", "question_segment_ids": ["s1"], "answer_segment_ids": ["s2"]}]
        projected = interview_models.transcript_roles(segments, pairs)
        self.assertEqual(projected[0]["qa_role"], "question")
        self.assertEqual(projected[1]["qa_role"], "answer")
        self.assertEqual(projected[1]["speaker"], "interviewer")
        self.assertNotIn("qa_role", segments[0])
        text = interviews.transcript_text(projected)
        self.assertIn("Question\nPlease introduce yourself.", text)
        self.assertIn("Answer\nMy research", text)
        self.assertNotIn("interviewer", text)
        self.assertIn("Speech\n", interviews.transcript_text(segments))

    def test_single_line_extended_key_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "key.txt"
            path.write_text("sk-test.part/with+symbols=", encoding="utf-8")
            with patch.dict(os.environ, {"QWEN_API_KEY": "", "DASHSCOPE_API_KEY": "", "QWEN_KEY_FILE": str(path)}):
                self.assertEqual(interview_models.qwen_config()[0], "sk-test.part/with+symbols=")

    def test_transcript_offsets_and_uncertain_words(self):
        chunk = {"index": 2, "start": 360, "duration": 180}
        result = interview_models.validate_transcript(chunk_response("I taught [unclear]."), chunk)
        self.assertEqual(result[0]["start"], 360)
        self.assertEqual(result[0]["end"], 361)
        self.assertTrue(result[0]["uncertain"])
        for time in (-1, float("nan"), 181):
            with self.assertRaises(ValueError):
                interview_models.validate_transcript(json.dumps({"segments": [{"start": time, "end": 182, "text": "x"}]}), chunk)

    def test_zero_duration_word_is_preserved_as_timestamp_anchor(self):
        chunk = {"index": 3, "start": 540, "duration": 180}
        rows = [{"start": 16.56, "end": 49.36, "text": "Would you join", "speaker": "interviewer"},
                {"start": 49.36, "end": 49.36, "text": "us", "speaker": "interviewer"}]
        result = interview_models.validate_transcript(json.dumps({"segments": rows}), chunk)
        self.assertEqual(result[1]["start"], 589.36)
        self.assertEqual(result[1]["end"], 589.36)
        self.assertEqual(result[1]["text"], "us")
        self.assertEqual(result[1]["id"], "c0003s0001")
        for start, end in [(-1, -1), (50, 49), (181, 181), (49, 182), (float("nan"), 50), (1, float("inf"))]:
            with self.assertRaises(ValueError):
                interview_models.validate_transcript(json.dumps({"segments": [{"start": start, "end": end, "text": "x"}]}), chunk)
        with self.assertRaises(ValueError):
            interview_models.validate_transcript(json.dumps({"segments": [rows[1], rows[0]]}), chunk)

    def test_qa_rejects_fabricated_segment_ids_or_question_matches(self):
        segments = [{"id": "s1", "start": 0, "end": 1, "text": "Question?", "uncertain": False}]
        for row in [
            {"kind": "qa", "question_segment_ids": ["invented"], "answer_segment_ids": []},
            {"kind": "qa", "question_segment_ids": ["s1"], "answer_segment_ids": [], "matched_question_id": "unknown"},
            {"kind": "qa", "question_segment_ids": ["s1"], "answer_segment_ids": ["s1"]},
        ]:
            with self.assertRaises(ValueError):
                interview_models.validate_qa(json.dumps({"pairs": [row]}), segments, {"questions": []})

    def test_statement_does_not_invent_a_question(self):
        segments = [{"id": "s1", "start": 0, "end": 1, "text": "Here is my work.", "uncertain": False}]
        result = interview_models.validate_qa(json.dumps({"pairs": [{"kind": "statement", "question_segment_ids": [], "answer_segment_ids": ["s1"]}]}), segments, {"questions": []})
        self.assertIsNone(result["qa"][0]["question"])
        self.assertEqual(result["qa"][0]["answer"], "Here is my work.")


class QwenStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / "0003.wav"
        self.audio.write_bytes(b"mock audio")
        config = patch.object(interview_models, "qwen_config", return_value=("test-key", "https://example.invalid/v1"))
        config.start()
        self.addCleanup(config.stop)

    def fake_client(self, lines, heartbeat=False):
        class Response:
            status_code = 200
            closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def aiter_lines(self):
                for line in lines:
                    yield line
                while True:
                    if heartbeat:
                        await asyncio.sleep(.005)
                        yield ": keepalive"
                    else:
                        await asyncio.Event().wait()

        response = Response()

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, *args, **kwargs):
                return response

        return Client(), response

    def event(self, text="", finish=None):
        return "data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": finish}]})

    async def test_finish_and_usage_close_stream_without_done(self):
        content = chunk_response("Actual words.")
        client, response = self.fake_client([self.event(content, "stop"), 'data: {"usage":{"total_tokens":20}}'])
        with patch.object(interview_models.httpx, "AsyncClient", return_value=client) as factory:
            result, receipt = await asyncio.wait_for(interview_models.transcribe_chunk(self.audio, 1), .5)
        self.assertEqual(result, content)
        self.assertEqual(receipt["usage"]["total_tokens"], 20)
        self.assertTrue(receipt["complete"] and response.closed)
        factory.assert_called_once()
        self.assertFalse(self.audio.with_suffix(".partial.txt").exists())

    async def test_finished_text_survives_missing_usage_and_done(self):
        content = chunk_response("Actual words.")
        client, response = self.fake_client([self.event(content, "stop")])
        with patch.object(interview_models.httpx, "AsyncClient", return_value=client), \
             patch.object(interview_models, "QWEN_USAGE_GRACE_SECONDS", .02):
            result, receipt = await asyncio.wait_for(interview_models.transcribe_chunk(self.audio, 1), .5)
        self.assertEqual(result, content)
        self.assertIsNone(receipt["usage"])
        self.assertTrue(receipt["complete"] and response.closed)

    async def test_heartbeats_cannot_extend_deadline_and_partial_is_not_checkpoint(self):
        client, response = self.fake_client([self.event('{"segments":[')], heartbeat=True)
        with patch.object(interview_models.httpx, "AsyncClient", return_value=client) as factory, \
             patch.object(interview_models, "QWEN_REQUEST_TIMEOUT_SECONDS", .03):
            with self.assertRaises(ai.AIError) as raised:
                await asyncio.wait_for(interview_models.transcribe_chunk(self.audio, 1), .5)
        self.assertIn("timed out", str(raised.exception))
        self.assertFalse(raised.exception.usage["complete"])
        self.assertEqual(self.audio.with_suffix(".partial.txt").read_text(), '{"segments":[')
        self.assertFalse(self.audio.with_suffix(".json").exists())
        self.assertFalse(json.loads(self.audio.with_suffix(".partial.receipt.json").read_text())["complete"])
        self.assertTrue(response.closed)
        factory.assert_called_once()

    async def test_cancellation_preserves_incomplete_text(self):
        client, response = self.fake_client([self.event("partial transcript")])
        with patch.object(interview_models.httpx, "AsyncClient", return_value=client):
            task = asyncio.create_task(interview_models.transcribe_chunk(self.audio, 1))
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.audio.with_suffix(".partial.txt").read_text(), "partial transcript")
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
