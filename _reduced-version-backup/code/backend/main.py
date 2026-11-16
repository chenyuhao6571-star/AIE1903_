import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai import AIError, generate_profile
from .education.facts import build_education_facts
from .education.job_requirements import extract_job_requirements
from .education.models import EducationAssessment, JobRequirements, ReviewAction
from .education.scoring import EducationScorer
from .extraction import MAX_BYTES, DocumentError, extract_document
from .schema import Profile

ROOT = Path(__file__).resolve().parents[1]
STORAGE = Path(os.getenv("CV_STORAGE", str(ROOT / "storage")))
JOBS_DIR = STORAGE / "jobs"
ASSESSMENT_FILE = "latest_assessment.json"
ASSESSMENT_DIR = "assessments"
LEGACY_ASSESSMENT_FILE = "education_assessment.json"
SAMPLE = ROOT.parent / "data" / "simple_dataset" / "CV.pdf"
TASKS: set[asyncio.Task] = set()
LIMIT = asyncio.Semaphore(2)


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(job_id):
    if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404, "Profile not found.")
    path = STORAGE / job_id
    if not (path / "job.json").exists():
        raise HTTPException(404, "Profile not found.")
    return path


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _validate_hex_id(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise HTTPException(404, f"{label} not found.")


def job_folder(job_id: str) -> Path:
    _validate_hex_id(job_id, "Job")
    path = JOBS_DIR / job_id
    if not (path / "job.json").exists():
        raise HTTPException(404, "Job description not found.")
    return path


def read_job_requirements(job_id: str) -> dict:
    return json.loads((job_folder(job_id) / "job.json").read_text(encoding="utf-8"))


def _assessment_path(candidate_dir: Path, job_id: str, version: int) -> Path:
    return candidate_dir / ASSESSMENT_DIR / job_id / f"{version:03d}.json"


def _next_assessment_version(candidate_dir: Path, job_id: str) -> int:
    version_dir = candidate_dir / ASSESSMENT_DIR / job_id
    if not version_dir.is_dir():
        return 1
    existing = [p.name for p in version_dir.glob("*.json") if p.name[:3].isdigit()]
    numbers = [int(name[:3]) for name in existing if len(name) >= 3]
    return (max(numbers) + 1) if numbers else 1


def create_job_requirements(text: str, title: str | None = None, filename: str | None = None, source_pages: list[int] | None = None) -> dict:
    clean = (text or "").strip()
    if not clean:
        raise HTTPException(400, "Provide a non-empty job description.")
    if len(clean) > MAX_BYTES:
        raise HTTPException(400, "Job description is too large.")
    job_id = uuid.uuid4().hex
    requirements = extract_job_requirements(clean, title, source_pages)
    path = JOBS_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "filename": filename or "job-description.txt",
        "title": requirements.title,
        "text": clean,
        "requirements": requirements.model_dump(),
        "created_at": now(),
        "updated_at": now(),
    }
    write_json(path / "job.json", job)
    return job


def read_job(job_id):
    return json.loads((folder(job_id) / "job.json").read_text(encoding="utf-8"))


def update_job(job_id, **changes):
    job = read_job(job_id)
    job.update(changes, updated_at=now())
    write_json(folder(job_id) / "job.json", job)
    return job


async def process(job_id):
    async with LIMIT:
        job = read_job(job_id)
        path = folder(job_id)
        try:
            update_job(job_id, status="extracting", error=None)
            content = (path / ("original" + job["extension"])).read_bytes()
            pages = await asyncio.to_thread(extract_document, content, job["filename"])
            write_json(path / "text.json", pages)
            update_job(job_id, status="analyzing", source_count=len(pages))
            profile = await generate_profile(pages, job["extension"] == ".docx")
            write_json(path / "profile.json", profile.model_dump())
            update_job(job_id, status="completed", name=profile.bio.name,
                       institution=profile.bio.institution, error=None)
        except (DocumentError, AIError) as exc:
            update_job(job_id, status="failed", error=str(exc))
        except asyncio.CancelledError:
            update_job(job_id, status="failed", error="Processing was interrupted. Please retry.")
            raise
        except Exception:
            # Never return upstream responses, credentials or document contents in errors.
            update_job(job_id, status="failed", error="Could not process this document. Please retry.")


def enqueue(job_id):
    task = asyncio.create_task(process(job_id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


@asynccontextmanager
async def lifespan(app):
    STORAGE.mkdir(parents=True, exist_ok=True)
    for saved in STORAGE.glob("*/job.json"):
        job = json.loads(saved.read_text(encoding="utf-8"))
        if job["status"] not in ("completed", "failed"):
            update_job(job["id"], status="failed", error="Processing was interrupted. Please retry.")
    yield
    pending = list(TASKS)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title="CV Reader", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "sample_available": SAMPLE.is_file()}


@app.get("/api/profiles")
def list_profiles():
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in STORAGE.glob("*/job.json")]
    return sorted(jobs, key=lambda j: j["created_at"], reverse=True)


def create_job(content, filename):
    filename = filename.replace("\\", "/").split("/")[-1][:180]
    extension = Path(filename).suffix.lower()
    if extension not in (".pdf", ".docx"):
        raise HTTPException(400, "Upload a PDF or .docx file. Save older .doc files as .docx first.")
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(400, "Choose a non-empty file smaller than 15 MB.")
    job_id = uuid.uuid4().hex
    path = STORAGE / job_id
    path.mkdir(parents=True)
    (path / ("original" + extension)).write_bytes(content)
    job = {"id": job_id, "filename": filename, "extension": extension, "status": "queued",
           "name": None, "institution": None, "error": None, "created_at": now(), "updated_at": now()}
    write_json(path / "job.json", job)
    enqueue(job_id)
    return job


@app.post("/api/upload", status_code=202)
async def upload(file: UploadFile = File(...)):
    try:
        content = await file.read(MAX_BYTES + 1)
        return create_job(content, file.filename or "document")
    finally:
        await file.close()


@app.post("/api/sample", status_code=202)
async def sample():
    if not SAMPLE.is_file():
        raise HTTPException(404, "Sample CV is unavailable. Upload a file instead.")
    return create_job(SAMPLE.read_bytes(), "Jialun Cao — CV.pdf")


@app.get("/api/profiles/{job_id}")
def get_profile(job_id: str):
    job = read_job(job_id)
    path = folder(job_id) / "profile.json"
    return {**job, "profile": json.loads(path.read_text(encoding="utf-8")) if job["status"] == "completed" and path.exists() else None}


@app.post("/api/profiles/{job_id}/retry", status_code=202)
async def retry(job_id: str):
    job = read_job(job_id)
    if job["status"] != "failed":
        raise HTTPException(409, "Only failed processing can be retried.")
    job = update_job(job_id, status="queued", error=None)
    enqueue(job_id)
    return job


@app.get("/api/profiles/{job_id}/document")
def document(job_id: str):
    job = read_job(job_id)
    return FileResponse(folder(job_id) / ("original" + job["extension"]), filename=job["filename"],
                        content_disposition_type="inline" if job["extension"] == ".pdf" else "attachment")


@app.get("/api/profiles/{job_id}/text")
def extracted_text(job_id: str):
    path = folder(job_id) / "text.json"
    if not path.exists():
        raise HTTPException(404, "Text is not available yet.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/profiles/{job_id}/json")
def download_json(job_id: str):
    path = folder(job_id) / "profile.json"
    if read_job(job_id)["status"] != "completed" or not path.exists():
        raise HTTPException(404, "Profile is not available yet.")
    return FileResponse(path, media_type="application/json", filename="candidate-profile.json")


class JobTextRequest(BaseModel):
    text: str
    title: str | None = None


class AnalyzeRequest(BaseModel):
    job_id: str


class ReviewRequest(BaseModel):
    component: str
    reviewer_score: float
    version: int | None = None
    reviewer_id: str | None = None
    reason: str | None = None
    note: str | None = None


@app.post("/api/jobs", status_code=201)
def create_job_text(payload: JobTextRequest):
    return create_job_requirements(payload.text, payload.title)


@app.post("/api/jobs/upload", status_code=201)
async def upload_job(file: UploadFile = File(...)):
    try:
        content = await file.read(MAX_BYTES + 1)
        if not content or len(content) > MAX_BYTES:
            raise HTTPException(400, "Choose a non-empty file smaller than 15 MB.")
        filename = (file.filename or "job-description.txt").replace("\\", "/").split("/")[-1][:180]
        extension = Path(filename).suffix.lower()
        if extension in (".pdf", ".docx"):
            pages = await asyncio.to_thread(extract_document, content, filename)
            text = "\n".join(page["text"] for page in pages)
            return create_job_requirements(text, filename=filename, source_pages=[page["number"] for page in pages])
        if extension in (".txt", ".md"):
            return create_job_requirements(content.decode("utf-8", errors="replace"), filename=filename)
        raise HTTPException(400, "Upload a PDF, .docx, .txt, or .md job description.")
    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return read_job_requirements(job_id)


@app.get("/api/jobs")
def list_jobs():
    jobs = []
    if JOBS_DIR.is_dir():
        for saved in JOBS_DIR.glob("*/job.json"):
            jobs.append(json.loads(saved.read_text(encoding="utf-8")))
    return sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)


@app.post("/api/education/analyze/{candidate_id}")
def analyze_education(candidate_id: str, payload: AnalyzeRequest):
    candidate_job = read_job(candidate_id)
    if candidate_job["status"] != "completed":
        raise HTTPException(409, "Candidate profile is not ready yet.")
    profile_path = folder(candidate_id) / "profile.json"
    if not profile_path.exists():
        raise HTTPException(409, "Candidate profile is not available.")
    profile = Profile.model_validate(json.loads(profile_path.read_text(encoding="utf-8")))
    job_doc = read_job_requirements(payload.job_id)
    requirements = JobRequirements.model_validate(job_doc["requirements"])
    facts = build_education_facts(profile, candidate_id)
    assessment = EducationScorer().assess(facts, requirements, payload.job_id)
    candidate_dir = folder(candidate_id)
    version = _next_assessment_version(candidate_dir, payload.job_id)
    assessment.version = version
    assessment_path = _assessment_path(candidate_dir, payload.job_id, version)
    assessment_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(assessment_path, assessment.model_dump())
    write_json(candidate_dir / ASSESSMENT_FILE, {"job_id": payload.job_id, "version": version, "updated_at": now()})
    return assessment.model_dump()


@app.get("/api/education/assessment/{candidate_id}")
def get_education_assessment(candidate_id: str):
    candidate_dir = folder(candidate_id)
    pointer = candidate_dir / ASSESSMENT_FILE
    legacy = candidate_dir / LEGACY_ASSESSMENT_FILE
    if not pointer.exists():
        if legacy.exists():
            return json.loads(legacy.read_text(encoding="utf-8"))
        raise HTTPException(404, "Education assessment is not available. Run the analyze endpoint first.")
    pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
    path = _assessment_path(candidate_dir, pointer_data["job_id"], pointer_data["version"])
    if not path.exists():
        raise HTTPException(404, "Education assessment is not available. Run the analyze endpoint first.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/education/evidence/{candidate_id}")
def get_education_evidence(candidate_id: str):
    data = get_education_assessment(candidate_id)
    evidence: list[dict] = []
    seen: set[str] = set()

    def add(items):
        for item in items:
            key = item.get("evidence_id") or item.get("claim")
            if key and key not in seen:
                seen.add(key)
                evidence.append(item)

    add(data.get("qualification_check", {}).get("evidence", []))
    for component in data.get("education_score", {}).get("components", {}).values():
        add(component.get("evidence", []))
    return {"candidate_id": candidate_id, "evidence": evidence}


@app.post("/api/education/review/{assessment_id}")
def review_education(assessment_id: str, payload: ReviewRequest):
    candidate_dir = folder(assessment_id)
    pointer = candidate_dir / ASSESSMENT_FILE
    legacy = candidate_dir / LEGACY_ASSESSMENT_FILE
    if not pointer.exists() and legacy.exists():
        path = legacy
    else:
        if not pointer.exists():
            raise HTTPException(404, "Education assessment is not available.")
        pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
        current_job_id = pointer_data["job_id"]
        current_version = pointer_data["version"]
        requested_version = payload.version or current_version
        if requested_version != current_version:
            raise HTTPException(409, "This assessment version is no longer the latest for this position. Refresh and review the current version.")
        path = _assessment_path(candidate_dir, current_job_id, current_version)
    if not path.exists():
        raise HTTPException(404, "Education assessment is not available.")
    assessment = EducationAssessment.model_validate(json.loads(path.read_text(encoding="utf-8")))
    components = assessment.education_score.components
    if payload.component not in components:
        raise HTTPException(400, f"Unknown component: {payload.component}.")
    if payload.reviewer_score < 0 or payload.reviewer_score > 10:
        raise HTTPException(400, "Reviewer score must be between 0 and 10.")

    component = components[payload.component]
    original_score = component.score
    old_final = assessment.education_score.final_score
    component.score = float(payload.reviewer_score)
    component.status = "reviewed"
    assessment.audit_trail.append(
        ReviewAction(
            component=payload.component,
            original_score=original_score,
            reviewer_score=payload.reviewer_score,
            reviewer_id=payload.reviewer_id,
            reason=payload.reason,
            note=payload.note,
            timestamp=now(),
        )
    )
    new_final, calculation = EducationScorer()._combine(components)
    assessment.education_score.final_score = round(new_final, 2) if new_final is not None else None
    assessment.education_score.calculation = calculation
    assessment.audit_trail.append(
        ReviewAction(
            component="final_score",
            original_score=old_final,
            reviewer_score=assessment.education_score.final_score,
            reviewer_id=payload.reviewer_id,
            reason="Recalculated after a component override.",
            note=None,
            timestamp=now(),
        )
    )
    assessment.review_status = "REVIEWED"
    assessment.updated_at = now()
    write_json(path, assessment.model_dump())
    return assessment.model_dump()


DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
