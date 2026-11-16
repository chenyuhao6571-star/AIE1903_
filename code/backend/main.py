import asyncio
import hashlib
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .ai import AIError, REQUEST_TRACE, estimate, generate_profile, generate_score, profile_messages, score_messages
from .extraction import MAX_BYTES, MAX_TEXT, DocumentError, extract_document
from .schema import Profile, Rubric
from .profile_tools import profile_tags, validate_correction
from .taxonomy import VOCABULARY
from . import criteria
from . import questions
from . import interviews
from . import interview_analysis
from .interview_demo import router as interview_demo_router
from .github import fetch_snapshot, username_from_url, profile_links, GitHubError

ROOT = Path(__file__).resolve().parents[1]
STORAGE = Path(os.getenv("CV_STORAGE", str(ROOT / "storage")))
SAMPLE = ROOT.parent / "data" / "simple_dataset" / "CV.pdf"
DEFAULT_RUBRIC = ROOT / "score.json"
MAX_DOCUMENTS = 12
TASKS: set[asyncio.Task] = set()
LOCKS = {}
LIMIT = asyncio.Semaphore(2)
ACTIVE = {"queued", "analyzing"}


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(job_id):
    if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404, "Candidate not found.")
    path = STORAGE / job_id
    if not (path / "job.json").exists():
        raise HTTPException(404, "Candidate not found.")
    return path


def lock(job_id):
    folder(job_id)
    return LOCKS.setdefault(job_id, asyncio.Lock())


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_job(job_id):
    return read_json(folder(job_id) / "job.json")


def update_job(job_id, **changes):
    job = read_job(job_id)
    job.update(changes, updated_at=now())
    write_json(folder(job_id) / "job.json", job)
    return job


def ensure_idle(job):
    if job["status"] in ACTIVE or job.get("score_status") in ACTIVE:
        raise HTTPException(409, "Wait for the current request to finish.")
    if questions.is_busy(job["id"]):
        raise HTTPException(409, "Wait for question generation to finish.")
    if interviews.is_busy(job["id"]):
        raise HTTPException(409, "Wait for interview processing to finish.")
    if interview_analysis.is_busy(job["id"]):
        raise HTTPException(409, "Wait for question analysis to finish.")


def sources(job):
    pages = []
    offset = 0
    for doc in job["documents"]:
        document_pages = read_json(folder(job["id"]) / doc["text_file"])
        for page in document_pages:
            pages.append({"number": offset + page["number"], "text": page["text"], "document_id": doc["id"],
                          "filename": doc["filename"], "page_number": page["number"],
                          "unit": "block" if doc["extension"] == ".docx" else "page"})
        offset += max((p["number"] for p in document_pages), default=0)
    if github := job.get('github'):
        for page in read_json(folder(job['id']) / github['file'])['pages']:
            pages.append({**page, 'number': offset + page['number'], 'page_number': page['number'], 'unit': 'github'})
    return pages


def rubric_for(job_id):
    template_id = read_job(job_id).get("selected_template_id")
    if not template_id:
        raise HTTPException(409, "Choose a criteria template before scoring.")
    return criteria.get_template(STORAGE, template_id)


def rubric_hash(rubric):
    return hashlib.sha256(rubric.model_dump_json().encode()).hexdigest()


def current_profile(job):
    path = folder(job["id"]) / "profile.json"
    if job.get("analyzed_revision") == job["revision"] and path.is_file():
        if edit := profile_edit(job):
            return Profile.model_validate(edit["profile"]).model_dump()
        return Profile.model_validate(read_json(path)).model_dump()
    return None


def profile_edit(job):
    path = folder(job["id"]) / "profile-edited.json"
    if path.is_file() and job.get("analyzed_revision") == job["revision"]:
        edit = read_json(path)
        if edit["revision"] == job["revision"]:
            return edit
    return None


def current_score(job, rubric):
    digest = rubric_hash(rubric)
    paths = [folder(job["id"]) / "scores" / f"{job['revision']}-{digest}.json",
             folder(job["id"]) / "score.json"]
    for path in paths:
        if path.is_file():
            result = read_json(path)
            if result.get("revision") == job["revision"] and result.get("rubric_hash") == digest:
                return result
    return None


def archive_score(job_id, result):
    directory = folder(job_id) / "scores"
    directory.mkdir(exist_ok=True)
    write_json(directory / f"{result['revision']}-{result['rubric_hash']}.json", result)


def saved_scores(job_id):
    directory = folder(job_id)
    results = {}
    # The latest-score file is also a fallback for results saved before archiving existed.
    for path in [directory / "score.json", *sorted((directory / "scores").glob("*.json"))]:
        if not path.is_file():
            continue
        result = read_json(path)
        identity = f"{result['revision']}-{result['rubric_hash']}"
        score_id = hashlib.sha256(identity.encode()).hexdigest()
        results[score_id] = result
    return sorted(results.items(), key=lambda item: item[1].get("created_at", ""), reverse=True)


def migrate_template_selection(path):
    job = read_json(path / "job.json")
    if "selected_template_id" in job:
        return
    selected = None
    saved_score = path / "score.json"
    if saved_score.is_file():
        result = read_json(saved_score)
        archive_score(job["id"], result)
        selected = criteria.import_template(STORAGE, Rubric.model_validate(result["rubric"]))["id"]
    custom = path / "rubric.json"
    if custom.is_file():
        selected = criteria.import_template(STORAGE, Rubric.model_validate_json(custom.read_text(encoding="utf-8-sig")))["id"]
    update_job(job["id"], selected_template_id=selected)


def invalidate(job_id, documents):
    job = read_job(job_id)
    return update_job(job_id, documents=documents, revision=job["revision"] + 1,
                      status="draft", error=None, score_status="none", score_error=None, institution=None)


def migrate_legacy(path):
    job = read_json(path / "job.json")
    if "documents" in job:
        return
    # Keep original files and the old profile. Only add dossier metadata.
    original = path / ("original" + job["extension"])
    text_path = path / "text.json"
    pages = read_json(text_path) if text_path.is_file() else []
    if not pages and original.is_file():
        try:
            pages = extract_document(original.read_bytes(), job["filename"])
            write_json(text_path, pages)
        except DocumentError:
            pass
    documents = []
    if original.is_file() and pages:
        documents = [{"id": "legacy", "filename": job["filename"], "extension": job["extension"],
                      "file": original.name, "text_file": "text.json", "size": original.stat().st_size,
                      "sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "source_count": len(pages),
                      "characters": sum(len(p["text"]) for p in pages)}]
    complete = job["status"] == "completed" and (path / "profile.json").is_file() and bool(documents)
    job.update(documents=documents, revision=1, analyzed_revision=1 if complete else None,
               status="completed" if complete else "draft", score_status="none", runs=[])
    write_json(path / "job.json", job)


def finish_run(job_id, run_id, state, receipt=None, error=None):
    job = read_job(job_id)
    runs = job.get("runs", [])
    for run in runs:
        if run["id"] == run_id:
            run.update(status=state, finished_at=now(), usage=receipt, error=error)
    update_job(job_id, runs=runs)


async def process(job_id, operation, run_id, rubric=None):
    trace_token = REQUEST_TRACE.set(lambda kind, data: write_json(folder(job_id) / f"request-{run_id}-{kind}.json", data))
    try:
        async with LIMIT:
            job = read_job(job_id)
            pages = sources(job)
            if operation == "analysis":
                update_job(job_id, status="analyzing")
                profile, receipt = await generate_profile(pages)
                write_json(folder(job_id) / "profile.json", profile.model_dump())
                update_job(job_id, status="completed", name=profile.bio.name, institution=profile.bio.institution,
                           analyzed_revision=job["revision"], error=None)
            else:
                update_job(job_id, score_status="analyzing")
                result, receipt = await generate_score(pages, current_profile(job), rubric)
                result.update(revision=job["revision"], rubric_hash=rubric_hash(rubric), created_at=now(),
                              template_id=job.get("selected_template_id"), source_snapshot=pages, run_id=run_id)
                archive_score(job_id, result)
                write_json(folder(job_id) / "score.json", result)
                update_job(job_id, score_status="completed", score_error=None)
            finish_run(job_id, run_id, "completed", receipt)
    except asyncio.CancelledError:
        changes = {"status": "failed", "error": "Request interrupted. Billing is unknown; retry only when ready."} if operation == "analysis" else {
            "score_status": "failed", "score_error": "Request interrupted. Billing is unknown; retry only when ready."}
        update_job(job_id, **changes)
        finish_run(job_id, run_id, "interrupted", error="Request interrupted. Billing is unknown.")
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, AIError) else "Could not finish the request. No automatic retry was made."
        changes = {"status": "failed", "error": message} if operation == "analysis" else {"score_status": "failed", "score_error": message}
        update_job(job_id, **changes)
        finish_run(job_id, run_id, "failed", exc.usage if isinstance(exc, AIError) else None, message)
    finally:
        REQUEST_TRACE.reset(trace_token)


@asynccontextmanager
async def lifespan(app):
    global LIMIT
    LIMIT = asyncio.Semaphore(2)
    LOCKS.clear()
    STORAGE.mkdir(parents=True, exist_ok=True)
    await questions.recover_interrupted()
    await interviews.startup()
    await interview_analysis.startup()
    for saved in STORAGE.glob("*/job.json"):
        migrate_legacy(saved.parent)
        migrate_template_selection(saved.parent)
        job = read_json(saved)
        changes = {}
        if job["status"] in ACTIVE:
            changes.update(status="failed", error="Request interrupted. No automatic retry was made; billing is unknown.")
        if job.get("score_status") in ACTIVE:
            changes.update(score_status="failed", score_error="Request interrupted. No automatic retry was made; billing is unknown.")
        for run in job.get("runs", []):
            if run["status"] == "running":
                run.update(status="interrupted", finished_at=now())
                changes["runs"] = job["runs"]
        if changes:
            update_job(job["id"], **changes)
    async def scan_existing():
        for saved in STORAGE.glob('*/job.json'):
            job_id = saved.parent.name
            async with lock(job_id):
                job = read_job(job_id)
                if job['status'] not in ACTIVE and job.get('score_status') not in ACTIVE:
                    await discover_github(job_id)
    task = asyncio.create_task(scan_existing())
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    yield
    await interview_analysis.shutdown()
    await questions.shutdown()
    await interviews.shutdown()
    pending = list(TASKS)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title="Candidate Dossier", lifespan=lifespan)
app.include_router(interview_demo_router)
app.include_router(questions.router)
app.include_router(interviews.router)
app.include_router(interview_analysis.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "sample_available": SAMPLE.is_file()}


@app.get("/api/profiles")
async def list_profiles():
    jobs = [read_json(p) for p in STORAGE.glob("*/job.json")]
    return sorted(jobs, key=lambda j: j["created_at"], reverse=True)


@app.get("/api/tag-search")
async def tag_search():
    rows, tags, excluded = [], {}, 0
    for path in STORAGE.glob("*/job.json"):
        job = read_json(path)
        profile = current_profile(job)
        if not profile:
            excluded += 1
            continue
        candidate_tags = profile_tags(profile)
        for tag in candidate_tags:
            tags.setdefault(tag["id"], {k: tag[k] for k in ("id", "category", "label")} | {"count": 0})["count"] += 1
        rows.append({"id": job["id"], "name": profile["bio"]["name"],
                     "institution": profile["bio"]["institution"],
                     "position": profile["bio"]["current_position"],
                     "manually_edited": bool(profile_edit(job)), "tags": candidate_tags})
    return {"rows": sorted(rows, key=lambda row: row["name"].casefold()),
            "tags": sorted(tags.values(), key=lambda tag: (tag["category"], tag["label"].casefold())),
            "excluded": excluded}


@app.get("/api/tag-vocabulary")
async def tag_vocabulary():
    return VOCABULARY


@app.delete("/api/profiles/{job_id}", status_code=204)
async def delete_candidate(job_id: str):
    async with lock(job_id):
        ensure_idle(read_job(job_id))
        shutil.rmtree(folder(job_id))
    # Keep the lock identity stable for any request already waiting on it.
    return Response(status_code=204)


class CandidateNotes(BaseModel):
    version: int = Field(ge=0)
    text: str = Field(max_length=20000)


@app.put("/api/profiles/{job_id}/notes")
async def save_notes(job_id: str, data: CandidateNotes):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        if data.version != job.get("notes_version", 0):
            raise HTTPException(409, "The notes changed. Reopen the editor to load the latest version.")
        update_job(job_id, notes=data.text.strip(), notes_version=data.version + 1, notes_updated_at=now())
        return {"version": data.version + 1}


class ProfileCorrection(BaseModel):
    revision: int = Field(ge=1)
    edit_version: int = Field(ge=0)
    section: str = Field(pattern=r"^(bio|education|work_experience|publications|achievements_awards|teaching|research|motivation|links)$")
    value: dict | list[dict]


@app.put("/api/profiles/{job_id}/profile")
async def correct_profile(job_id: str, data: ProfileCorrection):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        edit = profile_edit(job)
        version = edit["version"] if edit else 0
        if data.revision != job["revision"] or data.edit_version != version:
            raise HTTPException(409, "The profile changed. Reopen the editor to load the latest version.")
        original = current_profile(job)
        if original is None:
            raise HTTPException(409, "Analyze the current dossier before editing its profile.")
        if len(data.model_dump_json()) > 200_000:
            raise HTTPException(413, "This correction is too large. Shorten the section before saving.")
        try:
            corrected = validate_correction({**original, data.section: data.value}, data.section,
                                            {page["number"] for page in sources(job)})
        except ValidationError as exc:
            first = exc.errors()[0]
            raise HTTPException(422, f"{' / '.join(str(part) for part in first['loc'])}: {first['msg']}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if corrected == original:
            return {"version": version, "changed": False}
        edited_at = now()
        record = {"revision": job["revision"], "version": version + 1, "edited_at": edited_at,
                  "section": data.section, "before": original[data.section], "after": corrected[data.section]}
        directory = folder(job_id) / "profile-edits"
        directory.mkdir(exist_ok=True)
        write_json(directory / f"{job['revision']}-{version + 1}-{uuid.uuid4().hex}.json", record)
        write_json(folder(job_id) / "profile-edited.json",
                   {"revision": job["revision"], "version": version + 1, "edited_at": edited_at,
                    "profile": corrected})
        update_job(job_id, name=corrected["bio"]["name"], institution=corrected["bio"]["institution"])
        return {"version": version + 1, "changed": True}


@app.get("/api/templates")
async def list_templates():
    return criteria.list_templates(STORAGE)


@app.post("/api/templates", status_code=201)
async def create_template(rubric: Rubric):
    return criteria.import_template(STORAGE, rubric)


@app.put("/api/templates/{template_id}")
async def edit_template(template_id: str, rubric: Rubric):
    for saved in STORAGE.glob("*/job.json"):
        job = read_json(saved)
        if job.get("selected_template_id") == template_id and job.get("score_status") in ACTIVE:
            raise HTTPException(409, "This template is being used for scoring. Wait for the request to finish.")
    return criteria.save_template(STORAGE, rubric, template_id)


@app.get("/api/templates/{template_id}/json")
async def download_template(template_id: str):
    rubric = criteria.get_template(STORAGE, template_id)
    return Response(rubric.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="score.json"'})


@app.get("/api/comparison")
async def comparison(template_id: str | None = None):
    shared = criteria.get_template(STORAGE, template_id) if template_id else None
    rows = []
    for saved in STORAGE.glob("*/job.json"):
        job = read_json(saved)
        chosen_id = template_id or job.get("selected_template_id")
        rubric = shared
        if not rubric and chosen_id:
            try:
                rubric = criteria.get_template(STORAGE, chosen_id)
            except HTTPException:
                pass
        result = current_score(job, rubric) if rubric else None
        if result:
            status = "Scored"
        elif job.get("analyzed_revision") != job["revision"]:
            status = "Needs analysis"
        elif not chosen_id:
            status = "Choose template"
        elif chosen_id == job.get("selected_template_id") and job.get("score_status") in ACTIVE:
            status = "Scoring"
        elif chosen_id == job.get("selected_template_id") and job.get("score_status") == "failed":
            status = "Score failed"
        else:
            status = "Not scored"
        rows.append({"id": job["id"], "name": job["name"], "institution": job.get("institution"),
                     "template_id": chosen_id, "template_title": rubric.title if rubric else None,
                     "rubric_hash": rubric_hash(rubric) if rubric else None,
                     "status": status, "total": result["total"] if result else None,
                     "max_score": sum(s.max_score for s in rubric.sections) if rubric else None,
                     "sections": {s["section_id"]: s["score"] for s in result["sections"]} if result else {},
                     "created_at": result.get("created_at") if result else None})
    return {"template": criteria.describe(template_id, shared) if shared else None,
            "rows": sorted(rows, key=lambda row: row["name"].casefold())}


def request_summary(job, run):
    usage = run.get("usage") or {}
    return {**run, "candidate_id": job["id"], "candidate_name": job["name"],
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "cost_usd": usage.get("cost_usd")}


@app.get("/api/requests")
async def request_history():
    rows = []
    for saved in STORAGE.glob("*/job.json"):
        job = read_json(saved)
        rows.extend(request_summary(job, run) for run in job.get("runs", []))
    return sorted(rows, key=lambda row: row.get("started_at", ""), reverse=True)


@app.get("/api/requests/{job_id}/{run_id}")
async def request_detail(job_id: str, run_id: str):
    job = read_job(job_id)
    run = next((run for run in job.get("runs", []) if run["id"] == run_id), None)
    if not run or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise HTTPException(404, "Request not found.")
    detail = request_summary(job, run)
    for kind in ("input", "output"):
        path = folder(job_id) / f"request-{run_id}-{kind}.json"
        detail[kind] = read_json(path) if path.is_file() else None
    return detail


class CandidateInput(BaseModel):
    name: str = Field(default="", max_length=150)


class GitHubInput(BaseModel):
    url: str = Field(min_length=1, max_length=300)
    refresh: bool = False


async def save_github_snapshot(job_id, url, origin):
    job = read_job(job_id)
    snapshot = await fetch_snapshot(url)
    characters = sum(len(p['text']) for p in snapshot['pages'])
    if characters + sum(d['characters'] for d in job['documents']) > MAX_TEXT:
        raise GitHubError('GitHub and documents exceed 120,000 characters. Remove a document before importing.')
    filename = 'github-' + uuid.uuid4().hex + '.json'
    write_json(folder(job_id) / filename, snapshot)
    metadata = {k: v for k, v in snapshot.items() if k != 'pages'}
    return update_job(job_id, github={**metadata, 'file': filename, 'characters': characters}, github_origin=origin,
                      github_import_error=None, revision=job['revision'] + 1, status='draft',
                      score_status='none', error=None, score_error=None)


async def discover_github(job_id):
    """Caller holds the lock. Import clear profile links once per document set, without an LLM."""
    job = read_job(job_id)
    signature = hashlib.sha256(json.dumps([(d['id'], d.get('sha256')) for d in job['documents']]).encode()).hexdigest()
    if (job.get('github_discovery') or {}).get('signature') == signature:
        return job
    urls = set()
    for doc in job['documents']:
        pages = read_json(folder(job_id) / doc['text_file'])
        for page in pages:
            urls.update(profile_links(page['text']))
            for link in page.get('links', []):
                urls.update(profile_links(link))
        # Older stored text did not retain PDF annotations / DOCX hyperlink targets.
        original = folder(job_id) / doc.get('file', 'missing-original')
        if original.is_file() and not any('links' in page for page in pages):
            try:
                extracted = await asyncio.to_thread(extract_document, original.read_bytes(), doc['filename'])
                for page in extracted:
                    for link in page.get('links', []):
                        urls.update(profile_links(link))
            except DocumentError:
                pass
    candidates = sorted(urls)
    discovery = {'signature': signature, 'candidates': candidates}
    job = update_job(job_id, github_discovery=discovery, github_import_error=None)
    selected = (job.get('github') or {}).get('url')
    if selected and job.get('github_origin') != 'auto':
        return job  # Keep an explicit user choice.
    if selected and selected not in urls:
        job = update_job(job_id, github=None, revision=job['revision'] + 1, status='draft', score_status='none')
        selected = None
    if len(candidates) != 1 or selected == candidates[0] or candidates[0] in job.get('github_ignored_urls', []):
        return job
    try:
        return await save_github_snapshot(job_id, candidates[0], 'auto')
    except GitHubError as exc:
        return update_job(job_id, github_import_error=str(exc))
    except asyncio.CancelledError:
        update_job(job_id, github_import_error='GitHub import was interrupted. Retry from Links.')
        raise


@app.put('/api/profiles/{job_id}/github')
async def import_github(job_id: str, data: GitHubInput):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        try:
            username = username_from_url(data.url)
            if (job.get('github') or {}).get('username') == username and not data.refresh:
                return job
            return await save_github_snapshot(job_id, data.url, 'manual')
        except GitHubError as exc:
            raise HTTPException(400, str(exc)) from None


@app.delete('/api/profiles/{job_id}/github')
async def remove_github(job_id: str):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        if not job.get('github'):
            return job
        # Keep snapshots on disk, like removed document originals.
        return update_job(job_id, github=None, revision=job['revision'] + 1, status='draft',
                          github_ignored_urls=list(set(job.get('github_ignored_urls', []) + [job['github']['url']])),
                          github_import_error=None, score_status='none', error=None, score_error=None)


class RunInput(BaseModel):
    revision: int
    rubric_hash: str | None = None


class TemplateInput(BaseModel):
    template_id: str | None = None


@app.post("/api/profiles", status_code=201)
async def create_candidate(data: CandidateInput):
    job_id = uuid.uuid4().hex
    path = STORAGE / job_id
    path.mkdir(parents=True)
    job = {"id": job_id, "name": data.name.strip() or "Untitled candidate", "institution": None,
           "documents": [], "revision": 0, "analyzed_revision": None, "status": "draft",
           "score_status": "none", "error": None, "score_error": None, "runs": [],
           "selected_template_id": None,
           "created_at": now(), "updated_at": now()}
    write_json(path / "job.json", job)
    return job


async def add_documents(job_id, items):
    # Called with the candidate lock held. Validate the whole batch before writing.
    job = read_job(job_id)
    ensure_idle(job)
    if len(job["documents"]) + len(items) > MAX_DOCUMENTS:
        raise HTTPException(400, f"A dossier can contain at most {MAX_DOCUMENTS} documents.")
    hashes = {d["sha256"] for d in job["documents"]}
    prepared = []
    characters = sum(d["characters"] for d in job["documents"]) + (job.get('github') or {}).get('characters', 0)
    for filename, content in items:
        filename = filename.replace("\\", "/").split("/")[-1][:180]
        digest = hashlib.sha256(content).hexdigest()
        if digest in hashes:
            raise HTTPException(409, f"{filename} is already in this dossier or upload batch. Remove the duplicate and try again.")
        hashes.add(digest)
        try:
            pages = await asyncio.to_thread(extract_document, content, filename)
        except DocumentError as exc:
            raise HTTPException(400, f"{filename}: {exc}") from None
        characters += sum(len(p["text"]) for p in pages)
        if characters > MAX_TEXT:
            raise HTTPException(400, "The combined dossier exceeds 120,000 characters. Use shorter documents for one analysis request.")
        doc_id = uuid.uuid4().hex
        extension = Path(filename).suffix.lower()
        metadata = {"id": doc_id, "filename": filename, "extension": extension, "file": doc_id + extension,
                    "text_file": doc_id + ".json", "sha256": digest, "size": len(content),
                    "source_count": len(pages), "characters": sum(len(p["text"]) for p in pages)}
        prepared.append((metadata, content, pages))
    for doc, content, pages in prepared:
        (folder(job_id) / doc["file"]).write_bytes(content)
        write_json(folder(job_id) / doc["text_file"], pages)
    invalidate(job_id, job["documents"] + [d for d, _, _ in prepared])
    return await discover_github(job_id)


@app.post("/api/profiles/{job_id}/documents", status_code=201)
async def upload_documents(job_id: str, files: list[UploadFile] = File(...)):
    try:
        async with lock(job_id):
            if not files or len(files) > MAX_DOCUMENTS:
                raise HTTPException(400, f"Choose between 1 and {MAX_DOCUMENTS} documents.")
            items = [(f.filename or "document", await f.read(MAX_BYTES + 1)) for f in files]
            return await add_documents(job_id, items)
    finally:
        for file in files:
            await file.close()


@app.post("/api/profiles/{job_id}/sample", status_code=201)
async def add_sample(job_id: str):
    if not SAMPLE.is_file():
        raise HTTPException(404, "Sample CV is unavailable.")
    async with lock(job_id):
        return await add_documents(job_id, [("Sample CV.pdf", SAMPLE.read_bytes())])


@app.delete("/api/profiles/{job_id}/documents/{doc_id}")
async def remove_document(job_id: str, doc_id: str):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        documents = [d for d in job["documents"] if d["id"] != doc_id]
        if len(documents) == len(job["documents"]):
            raise HTTPException(404, "Document not found.")
        # Detach only. Retain originals on disk to prevent accidental data loss.
        invalidate(job_id, documents)
        return await discover_github(job_id)


@app.get("/api/profiles/{job_id}")
async def get_profile(job_id: str):
    job = read_job(job_id)
    edit = profile_edit(job)
    rubric_error = None
    try:
        rubric = rubric_for(job_id)
    except HTTPException as exc:
        rubric, rubric_error = None, exc.detail if job.get("selected_template_id") else None
    score = current_score(job, rubric) if rubric else None
    score_status = "completed" if score else "stale" if job.get("score_status") == "completed" else job.get("score_status")
    return {**job, "score_status": score_status, "profile": current_profile(job), "score": score,
            "profile_edit_version": edit["version"] if edit else 0,
            "profile_edited_at": edit["edited_at"] if edit else None,
            "rubric": rubric.model_dump() if rubric else None, "rubric_error": rubric_error,
            "rubric_hash": rubric_hash(rubric) if rubric else None,
            "sources": [{k: v for k, v in p.items() if k != "text"} for p in sources(job)]}


@app.get("/api/profiles/{job_id}/estimate")
async def get_estimate(job_id: str, operation: str = "analysis"):
    job = read_job(job_id)
    pages = sources(job)
    if not pages:
        raise HTTPException(400, "Add documents before estimating the cost.")
    if operation == "analysis":
        result = estimate(profile_messages(pages), operation)
    elif operation == "score":
        profile = current_profile(job)
        if not profile:
            raise HTTPException(409, "Analyze the current dossier before scoring it.")
        rubric = rubric_for(job_id)
        result = estimate(score_messages(pages, profile, rubric), operation)
        result["rubric_hash"] = rubric_hash(rubric)
    else:
        raise HTTPException(400, "Choose analysis or score.")
    return {**result, "revision": job["revision"]}


async def launch(job_id, operation, data):
    async with lock(job_id):
        job = read_job(job_id)
        ensure_idle(job)
        if data.revision != job["revision"]:
            raise HTTPException(409, "The dossier changed. Refresh the cost estimate before running.")
        if not sources(job):
            raise HTTPException(400, "Add documents or import GitHub first.")
        rubric = None
        if operation == "analysis":
            if current_profile(job):
                raise HTTPException(409, "This dossier is already analyzed. Add or remove a document to update it.")
            changes = {"status": "queued", "error": None}
        else:
            if not current_profile(job):
                raise HTTPException(409, "Analyze the current dossier before scoring it.")
            rubric = rubric_for(job_id)
            if data.rubric_hash != rubric_hash(rubric):
                raise HTTPException(409, "The rubric changed. Refresh the cost estimate before scoring.")
            if current_score(job, rubric):
                raise HTTPException(409, "This dossier is already scored with these criteria.")
            changes = {"score_status": "queued", "score_error": None}
        run_id = uuid.uuid4().hex
        runs = job.get("runs", []) + [{"id": run_id, "operation": operation, "status": "running", "started_at": now(), "usage": None,
                                       "template_title": rubric.title if rubric else None,
                                       "template_id": job.get("selected_template_id") if rubric else None}]
        update_job(job_id, **changes, runs=runs)
        task = asyncio.create_task(process(job_id, operation, run_id, rubric))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
        return read_job(job_id)


@app.post("/api/profiles/{job_id}/analyze", status_code=202)
async def analyze(job_id: str, data: RunInput):
    return await launch(job_id, "analysis", data)


@app.post("/api/profiles/{job_id}/score", status_code=202)
async def score(job_id: str, data: RunInput):
    return await launch(job_id, "score", data)


async def select_template(job_id, template_id):
    rubric = criteria.get_template(STORAGE, template_id) if template_id else None
    job = read_job(job_id)
    ensure_idle(job)
    result = current_score(job, rubric) if rubric else None
    return update_job(job_id, selected_template_id=template_id,
                      score_status="completed" if result else "none", score_error=None)


@app.put("/api/profiles/{job_id}/template")
async def choose_template(job_id: str, data: TemplateInput):
    async with lock(job_id):
        return await select_template(job_id, data.template_id)


@app.put("/api/profiles/{job_id}/rubric")
async def upload_rubric(job_id: str, file: UploadFile = File(...)):
    try:
        async with lock(job_id):
            job = read_job(job_id)
            ensure_idle(job)
            raw = await file.read(100_001)
            if len(raw) > 100_000:
                raise HTTPException(400, "The score file must be smaller than 100 KB.")
            try:
                rubric = Rubric.model_validate_json(raw.decode("utf-8-sig"))
            except (ValidationError, ValueError, UnicodeError):
                raise HTTPException(400, "Invalid score file. Use the JSON template: a title and unique sections with id, title, max_score and rules.") from None
            template = criteria.import_template(STORAGE, rubric)
            await select_template(job_id, template["id"])
            return rubric.model_dump()
    finally:
        await file.close()


@app.get("/api/profiles/{job_id}/rubric")
async def download_rubric(job_id: str):
    rubric = rubric_for(job_id)
    return Response(rubric.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="score.json"'})


@app.get("/api/profiles/{job_id}/documents/{doc_id}")
async def document(job_id: str, doc_id: str):
    job = read_job(job_id)
    doc = next((d for d in job["documents"] if d["id"] == doc_id), None)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    return FileResponse(folder(job_id) / doc["file"], filename=doc["filename"],
                        content_disposition_type="inline" if doc["extension"] == ".pdf" else "attachment")


@app.get("/api/profiles/{job_id}/text")
async def extracted_text(job_id: str):
    return sources(read_job(job_id))


@app.get("/api/profiles/{job_id}/json")
async def download_json(job_id: str):
    job = read_job(job_id)
    profile = current_profile(job)
    if profile is None:
        raise HTTPException(404, "Analyze the current dossier first.")
    # Original profile export remains usable by downstream consumers.
    return Response(json.dumps(current_profile(job), ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="candidate-profile.json"'})


@app.get("/api/profiles/{job_id}/score.json")
async def download_score(job_id: str):
    job = read_job(job_id)
    result = current_score(job, rubric_for(job_id))
    if not result:
        raise HTTPException(404, "No score is available for the current dossier and rubric.")
    return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="candidate-score.json"'})


@app.get("/api/profiles/{job_id}/scores")
async def score_history(job_id: str):
    job = read_job(job_id)
    try:
        digest = rubric_hash(rubric_for(job_id))
    except HTTPException:
        digest = None
    return [{"id": score_id, "created_at": result.get("created_at"),
             "template_title": result["rubric"]["title"], "revision": result["revision"],
             "total": result["total"], "max_score": result["max_score"],
             "is_current": result["revision"] == job["revision"] and result["rubric_hash"] == digest}
            for score_id, result in saved_scores(job_id)]


@app.get("/api/profiles/{job_id}/scores/{score_id}")
async def saved_assessment(job_id: str, score_id: str, download: bool = False):
    result = next((value for key, value in saved_scores(job_id) if key == score_id), None)
    if result is None:
        raise HTTPException(404, "Saved assessment not found.")
    if download:
        return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="saved-assessment.json"'})
    return result


DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
