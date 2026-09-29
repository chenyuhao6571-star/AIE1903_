import io
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi.testclient import TestClient

from backend import main
from backend.ai import AIError
from backend.extraction import DocumentError, extract_document
from backend.schema import Profile


def word_bytes():
    doc = Document()
    doc.add_paragraph("Alex Example: researcher in software testing and program verification.")
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


class ExtractionTests(unittest.TestCase):
    @unittest.skipUnless(main.SAMPLE.is_file(), "Optional local sample CV is not included in Git")
    def test_real_cv_has_seven_readable_pages(self):
        pages = extract_document(main.SAMPLE.read_bytes(), "CV.pdf")
        self.assertEqual(len(pages), 7)
        self.assertIn("Jialun Cao", pages[0]["text"])
        self.assertIn("1,590,700", pages[1]["text"])

    def test_word_preserves_paragraph_and_table_order(self):
        pages = extract_document(word_bytes(), "CV.docx")
        self.assertEqual(len(pages), 3)
        self.assertIn("Education | PhD", pages[1]["text"])
        self.assertIn("Work experience", pages[2]["text"])

    def test_unreadable_files_and_empty_docs(self):
        for data, name in [(b"corrupt", "cv.pdf"), (b"legacy", "cv.doc"), (b"", "cv.pdf")]:
            with self.subTest(name=name), self.assertRaises(DocumentError):
                extract_document(data, name)
        doc = Document()
        buffer = io.BytesIO()
        doc.save(buffer)
        with self.assertRaisesRegex(DocumentError, "No readable text"):
            extract_document(buffer.getvalue(), "empty.docx")


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(__file__).resolve().parents[2] / "tmp_backend_api_tests"
        shutil.rmtree(self.temp, ignore_errors=True)
        self.temp.mkdir(parents=True, exist_ok=True)
        self.storage = patch.object(main, "STORAGE", self.temp)
        self.storage.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.storage.stop()
        shutil.rmtree(self.temp, ignore_errors=True)

    def wait_job(self, job_id):
        for _ in range(100):
            job = self.client.get(f"/api/profiles/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(.03)
        self.fail("Job did not finish")

    def test_upload_extract_generate_persist_and_download(self):
        with patch.object(main, "generate_profile", AsyncMock(return_value=PROFILE)) as ai:
            uploaded = word_bytes()
            response = self.client.post("/api/upload", files={"file": ("CV.docx", uploaded)})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            job = self.wait_job(job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["profile"]["bio"]["name"], "Alex Example")
            ai.assert_awaited_once()
            self.assertEqual(len(self.client.get(f"/api/profiles/{job_id}/text").json()), 3)
            self.assertEqual(self.client.get(f"/api/profiles/{job_id}/json").json(), PROFILE.model_dump())
            self.assertEqual(self.client.get(f"/api/profiles/{job_id}/document").content, uploaded)
            self.assertEqual(self.client.post(f"/api/profiles/{job_id}/retry").status_code, 409)

    def test_upstream_failure_is_retryable(self):
        with patch.object(main, "generate_profile", AsyncMock(side_effect=AIError("Service unavailable. Retry."))):
            response = self.client.post("/api/upload", files={"file": ("CV.docx", word_bytes())})
            job_id = response.json()["id"]
            self.assertEqual(self.wait_job(job_id)["status"], "failed")
            self.assertEqual(self.client.get(f"/api/profiles/{job_id}/json").status_code, 404)
        with patch.object(main, "generate_profile", AsyncMock(return_value=PROFILE)):
            self.assertEqual(self.client.post(f"/api/profiles/{job_id}/retry").status_code, 202)
            self.assertEqual(self.wait_job(job_id)["status"], "completed")

    def test_invalid_upload_and_unknown_id(self):
        self.assertEqual(self.client.post("/api/upload", files={"file": ("cv.exe", b"test")}).status_code, 400)
        self.assertEqual(self.client.get("/api/profiles/not-a-job/document").status_code, 404)


if __name__ == "__main__":
    unittest.main()
