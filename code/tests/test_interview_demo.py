import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import interview_demo


class InterviewDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Screening interview.mp4").write_bytes(b"0123456789")
        (self.root / "Formal interview.mp4").write_bytes(b"abcdefghij")
        (self.root / "private.txt").write_text("not a video")
        self.patch = patch.object(interview_demo, "VIDEO_DIR", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        app = FastAPI()
        app.include_router(interview_demo.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_lists_only_videos_with_screening_first(self):
        response = self.client.get("/api/interview-demo/videos")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([v["name"] for v in response.json()],
                         ["Screening interview.mp4", "Formal interview.mp4"])

    def test_range_requests_support_seeking(self):
        response = self.client.get("/api/interview-demo/videos/Screening%20interview.mp4",
                                   headers={"Range": "bytes=3-6"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"3456")
        self.assertEqual(response.headers["content-range"], "bytes 3-6/10")
        self.assertEqual(response.headers["content-type"], "video/mp4")

    def test_does_not_serve_non_video_or_outside_paths(self):
        for filename in ("private.txt", "missing.mp4", "..%2Fprivate.txt", "..%5Cprivate.txt"):
            self.assertEqual(self.client.get(f"/api/interview-demo/videos/{filename}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
