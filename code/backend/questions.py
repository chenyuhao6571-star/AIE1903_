"""Persistent, explicitly generated interview questions and committee review."""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import ai
from .questions_prompt import FACTORS, question_messages

router = APIRouter(prefix="/api/profiles/{job_id}/questions")
TASKS = {}
FactorId = Literal["teaching", "awards", "service", "research", "publications"]
Text = Annotated[str, Field(min_length=1, max_length=1000)]


def host():
    # main includes this router only after defining its storage helpers.
    from . import main
    return main


def now():
    return datetime.now(timezone.utc).isoformat()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(Strict):
    source_id: int = Field(gt=0)
    quote: str = Field(min_length=5, max_length=800)


class Dimension(Strict):
    id: str
    supported: bool
    detail: str = Field(min_length=1, max_length=400)
    evidence: list[Evidence] = Field(max_length=3)


class FactorResult(Strict):
    id: FactorId
    dimensions: list[Dimension] = Field(min_length=4, max_length=4)


class GeneratedQuestion(Strict):
    factor: FactorId
    clarification: Text
    text: str = Field(min_length=5, max_length=2000)
    criteria: list[Text] = Field(min_length=2, max_length=5)
    evidence_refs: list[Evidence] = Field(max_length=5)


class GeneratedResult(Strict):
    factors: list[FactorResult] = Field(min_length=5, max_length=5)
    questions: list[GeneratedQuestion] = Field(min_length=5, max_length=10)


class QuestionInput(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    factor: FactorId
    text: str = Field(min_length=5, max_length=2000)
    clarification: Text
    criteria: list[Text] = Field(min_length=1, max_length=10)
    approved: bool = False
    sources: list[int] = Field(default_factory=list, max_length=30)
    evidence: str = Field(default="", max_length=5000)
    evidence_refs: list[Evidence] = Field(default_factory=list, max_length=5)
    origin: Literal["model", "manual"] = "manual"


class SaveInput(Strict):
    version: int = Field(ge=0)
    questions: list[QuestionInput] = Field(max_length=60)


class GenerateInput(Strict):
    version: int = Field(ge=0)
    context_hash: str
    replace: bool = False


def empty_state():
    return {"version": 0, "status": "empty", "error": None, "questions": [], "factors": [],
            "context_hash": None, "generated_at": None, "usage": None}


def directory(job_id):
    return host().folder(job_id) / "questions"


def read_state(job_id):
    path = directory(job_id) / "state.json"
    return host().read_json(path) if path.is_file() else empty_state()


def write_state(job_id, state):
    directory(job_id).mkdir(exist_ok=True)
    host().write_json(directory(job_id) / "state.json", state)


def context(job_id):
    app = host()
    job = app.read_job(job_id)
    profile = app.current_profile(job)
    if profile is None:
        raise HTTPException(409, "Analyze the current dossier first.")
    rubric = app.rubric_for(job_id).model_dump()
    pages = app.sources(job)
    value = {"profile": profile, "rubric": rubric, "pages": pages, "revision": job["revision"]}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return value, digest


def public_state(job_id, state=None):
    state = read_state(job_id) if state is None else state
    try:
        current, digest = context(job_id)
        error = None
    except HTTPException as exc:
        current, digest, error = None, None, str(exc.detail)
    exposed = {key: value for key, value in state.items() if key != "deleted_questions"}
    return {**exposed, "stale": bool(state.get("context_hash") and state["context_hash"] != digest),
            "current_context_hash": digest, "can_generate": current is not None, "context_error": error}


def current_snapshot(job_id):
    state = public_state(job_id)
    return {"version": state["version"], "context_hash": state["context_hash"], "stale": state["stale"],
            "questions": [] if state["stale"] else state["questions"]}


def approved_snapshot(job_id):
    snapshot = current_snapshot(job_id)
    snapshot["questions"] = [q for q in snapshot["questions"] if q["approved"]]
    return snapshot


def is_busy(job_id):
    return read_state(job_id)["status"] == "generating"


def validate_evidence(evidence, pages, corrections=None):
    by_id = {p["number"]: p for p in pages}
    for ref in evidence:
        source = by_id.get(ref.source_id)
        if source is not None and ai.quote_in_source(ref.quote, source["text"]):
            continue
        # The model can confuse adjacent SOURCE IDs. Correct only the reference,
        # never the quote: it must occur uniquely in the same submitted document.
        document_key = (source.get("document_id") or source.get("filename")) if source else None
        matches = [page for page in pages
                   if document_key and (page.get("document_id") or page.get("filename")) == document_key
                   and page.get("unit", "page") == source.get("unit", "page")
                   and ai.quote_in_source(ref.quote, page["text"])]
        if len(matches) != 1:
            raise ValueError(f"Evidence does not match source {ref.source_id}.")
        correction = {"from_source_id": ref.source_id, "to_source_id": matches[0]["number"], "quote": ref.quote}
        if corrections is not None and correction not in corrections:
            corrections.append(correction)
        ref.source_id = matches[0]["number"]


def validate_result(content, pages, corrections=None):
    result = GeneratedResult.model_validate_json(content)
    expected = {factor["id"]: factor for factor in FACTORS}
    if len({factor.id for factor in result.factors}) != 5:
        raise ValueError("Coverage factors must occur exactly once.")
    factors = []
    for row in result.factors:
        definition = expected[row.id]
        dimensions = {dimension.id: dimension for dimension in row.dimensions}
        if set(dimensions) != {key for key, _ in definition["dimensions"]}:
            raise ValueError(f"Invalid coverage dimensions for {row.id}.")
        for dimension in dimensions.values():
            if dimension.supported != bool(dimension.evidence):
                raise ValueError("Supported coverage dimensions require evidence; missing dimensions must not invent evidence.")
            validate_evidence(dimension.evidence, pages, corrections)
        factors.append({"id": row.id, "title": definition["title"],
                        "coverage": sum(d.supported for d in dimensions.values()) * 25,
                        "dimensions": [{**dimensions[key].model_dump(), "title": title} for key, title in definition["dimensions"]]})
    order = {factor["id"]: i for i, factor in enumerate(FACTORS)}
    factors.sort(key=lambda factor: order[factor["id"]])
    questions = []
    for row in result.questions:
        validate_evidence(row.evidence_refs, pages, corrections)
        refs = [ref.model_dump() for ref in row.evidence_refs]
        questions.append({**row.model_dump(), "id": uuid.uuid4().hex, "approved": False, "origin": "model",
                          "sources": list(dict.fromkeys(ref["source_id"] for ref in refs)),
                          "evidence": "\n".join(ref["quote"] for ref in refs)})
    coverage = {factor["id"]: factor["coverage"] for factor in factors}
    questions.sort(key=lambda q: coverage[q["factor"]])
    return factors, questions


async def process(job_id, run_id, snapshot, digest):
    app = host()
    run_dir = directory(job_id) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    def trace(kind, value):
        app.write_json(run_dir / f"{kind}.json", value)
    token = ai.REQUEST_TRACE.set(trace)
    receipt = None
    try:
        async with app.LIMIT:
            content, receipt = await ai.call_json(question_messages(snapshot["profile"], snapshot["rubric"], snapshot["pages"], GeneratedResult.model_json_schema()))
        corrections = []
        factors, questions = validate_result(content, snapshot["pages"], corrections)
        async with app.lock(job_id):
            state = read_state(job_id)
            if state.get("run_id") != run_id:
                return
            # Keep results as a versioned snapshot even if the context changed elsewhere.
            state.update(version=state["version"] + 1, status="completed", error=None, factors=factors, questions=questions,
                         context_hash=digest, generated_at=now(), usage=receipt, updated_at=now(), deleted_questions={})
            write_state(job_id, state)
            app.write_json(run_dir / "result.json", {"context_hash": digest, "factors": factors, "questions": questions, "usage": receipt,
                                                    "source_corrections": corrections})
    except asyncio.CancelledError:
        await fail(job_id, run_id, "Generation interrupted. No automatic retry was made.", receipt, "interrupted")
        raise
    except (ai.AIError, ValidationError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ai.AIError) else "Question generation failed validation. No automatic retry was made."
        if isinstance(exc, ValueError) and not isinstance(exc, ValidationError):
            message = f"Question generation failed validation: {exc} No automatic retry was made."
        await fail(job_id, run_id, message, getattr(exc, "usage", None) or receipt)
    except Exception:
        await fail(job_id, run_id, "Question generation failed. No automatic retry was made.", receipt)
    finally:
        ai.REQUEST_TRACE.reset(token)
        TASKS.pop(job_id, None)


async def fail(job_id, run_id, message, receipt=None, status="failed"):
    try:
        async with host().lock(job_id):
            state = read_state(job_id)
            if state.get("run_id") == run_id:
                state.update(version=state["version"] + 1, status=status, error=message, usage=receipt, updated_at=now())
                write_state(job_id, state)
                host().write_json(directory(job_id) / "runs" / run_id / "failure.json", {"error": message, "usage": receipt})
    except (HTTPException, OSError):
        pass


async def recover_interrupted():
    for path in host().STORAGE.glob("*/questions/state.json"):
        state = host().read_json(path)
        if state.get("status") == "generating":
            state.update(status="interrupted", error="Generation interrupted. No automatic retry was made.", version=state["version"] + 1)
            host().write_json(path, state)


async def recover_saved_result(job_id, run_id):
    """Explicit local recovery of the latest saved response; never calls an API."""
    app = host()
    async with app.lock(job_id):
        state = read_state(job_id)
        if state["status"] == "generating" or state.get("run_id") != run_id:
            raise ValueError("Only the latest inactive question request can be recovered.")
        if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("Invalid question request ID.")
        run_dir = directory(job_id) / "runs" / run_id
        if state["status"] == "completed" and (run_dir / "result.json").is_file():
            result = app.read_json(run_dir / "result.json")
            return {"questions": len(state["questions"]), "source_corrections": result.get("source_corrections", []), "version": state["version"]}
        snapshot = app.read_json(run_dir / "context.json")
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        _, current_digest = context(job_id)
        if digest != current_digest:
            raise ValueError("The dossier or position changed after this request.")
        output = app.read_json(run_dir / "output.json")
        if output.get("finish_reason") != "stop":
            raise ValueError("The saved response did not finish successfully.")
        corrections = []
        factors, rows = validate_result(output["content"], snapshot["pages"], corrections)
        receipt = state.get("usage") or ai.usage_receipt(output)
        recovered_at = now()
        app.write_json(run_dir / "result.json", {"context_hash": digest, "factors": factors, "questions": rows,
                                                "usage": receipt, "source_corrections": corrections,
                                                "recovered_at": recovered_at})
        state.update(version=state["version"] + 1, status="completed", error=None, factors=factors, questions=rows,
                     context_hash=digest, generated_at=recovered_at, usage=receipt, updated_at=recovered_at, deleted_questions={})
        write_state(job_id, state)
        return {"questions": len(rows), "source_corrections": corrections, "version": state["version"]}


async def shutdown():
    tasks = list(TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@router.get("")
async def get_questions(job_id: str):
    return public_state(job_id)


@router.get("/estimate")
async def estimate_questions(job_id: str):
    snapshot, digest = context(job_id)
    messages = question_messages(snapshot["profile"], snapshot["rubric"], snapshot["pages"], GeneratedResult.model_json_schema())
    return {**ai.estimate(messages, "questions"), "version": read_state(job_id)["version"], "context_hash": digest}


@router.post("/generate", status_code=202)
async def generate_questions(job_id: str, data: GenerateInput):
    app = host()
    async with app.lock(job_id):
        state = read_state(job_id)
        if state["status"] == "generating":
            raise HTTPException(409, "Question generation is already running.")
        app.ensure_idle(app.read_job(job_id))
        if data.version != state["version"]:
            raise HTTPException(409, "Questions changed. Reload and retry.")
        snapshot, digest = context(job_id)
        if data.context_hash != digest:
            raise HTTPException(409, "The dossier or position changed. Refresh the estimate.")
        if state["context_hash"] == digest and state.get("generated_at") and not data.replace:
            return JSONResponse(public_state(job_id, state))
        run_id = uuid.uuid4().hex
        directory(job_id).mkdir(exist_ok=True)
        (directory(job_id) / "runs" / run_id).mkdir(parents=True)
        app.write_json(directory(job_id) / "runs" / run_id / "context.json", snapshot)
        state.update(version=state["version"] + 1, status="generating", error=None, run_id=run_id, updated_at=now())
        write_state(job_id, state)
        TASKS[job_id] = asyncio.create_task(process(job_id, run_id, snapshot, digest))
        return public_state(job_id, state)


@router.put("")
async def save_questions(job_id: str, data: SaveInput):
    app = host()
    async with app.lock(job_id):
        state = read_state(job_id)
        if state["status"] == "generating":
            raise HTTPException(409, "Wait for question generation to finish.")
        app.ensure_idle(app.read_job(job_id))
        if data.version != state["version"]:
            raise HTTPException(409, "Questions changed. Reload and retry.")
        snapshot, digest = context(job_id)
        if state["context_hash"] and digest != state["context_hash"]:
            raise HTTPException(409, "The dossier or position changed. Generate new questions first.")
        ids = [q.id for q in data.questions]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Question IDs must be unique.")
        current_rows = {q["id"]: q for q in state["questions"]}
        retired = state.get("deleted_questions", {})
        previous = {**retired, **current_rows}
        rows = []
        for item in data.questions:
            row = item.model_dump()
            old = previous.get(item.id)
            if old:
                # Source evidence is owned by the generation result, not editable prose.
                for key in ("sources", "evidence", "evidence_refs", "origin"):
                    row[key] = old.get(key, [] if key in {"sources", "evidence_refs"} else "")
                if any(row[key] != old[key] for key in ("text", "factor", "clarification", "criteria")):
                    row["approved"] = False
            else:
                row.update(approved=False, sources=[], evidence="", evidence_refs=[], origin="manual")
            rows.append(row)
        retired.update({key: value for key, value in current_rows.items() if key not in ids})
        retired = {key: value for key, value in list(retired.items())[-60:] if key not in ids}
        state.update(version=state["version"] + 1, status="completed", error=None, context_hash=digest, questions=rows, updated_at=now(), deleted_questions=retired)
        write_state(job_id, state)
        return public_state(job_id, state)
