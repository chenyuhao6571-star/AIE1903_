"""Persistent candidate interview processing. Paid steps run only on explicit POST."""

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import ai
from . import interview_media as media
from . import interview_models as models

router = APIRouter()
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data"
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}
TASKS = {}
IMPORT_LOCKS = {}
PROCESS_LIMIT = asyncio.Semaphore(1)


def main_module():
    from . import main
    return main


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def interview_root(job_id):
    return main_module().folder(job_id) / "interviews"


def directory(job_id, interview_id):
    if not re.fullmatch(r"[a-f0-9]{32}", interview_id):
        raise HTTPException(404, "Interview not found.")
    root = interview_root(job_id)
    path = root / interview_id
    if not (path / "state.json").is_file() or path.resolve().parent != root.resolve():
        raise HTTPException(404, "Interview not found.")
    return path


def read_record(job_id, interview_id):
    return read_json(directory(job_id, interview_id) / "state.json")


def update_record(path, **changes):
    record = read_json(path / "state.json")
    record.update(changes, updated_at=now())
    write_json(path / "state.json", record)
    return record


def is_busy(job_id):
    return any(candidate == job_id and not task.done() for (candidate, _), task in TASKS.items())


def samples():
    root = SAMPLE_DIR.resolve()
    return sorted((path for path in root.glob("*") if path.suffix.lower() in EXTENSIONS
                   and path.is_file() and path.resolve().parent == root),
                  key=lambda path: ("screening" not in path.name.lower(), path.name.lower()))


def available_segments(path):
    if (path / "transcript.json").is_file():
        return read_json(path / "transcript.json")["segments"]
    segments = []
    for chunk_path in sorted((path / "chunks").glob("[0-9][0-9][0-9][0-9].json")):
        segments.extend(read_json(chunk_path).get("segments", []))
    return segments


def public_record(record, path, detail=False):
    result = {key: value for key, value in record.items() if key not in {"video_file", "sha256", "chunks"}}
    result.update(has_audio=(path / "audio.wav").is_file(),
                  has_transcript=(path / "transcript.json").is_file(), has_qa=(path / "qa.json").is_file())
    if detail:
        segments = available_segments(path)
        result["transcript"] = {"segments": segments, "complete": result["has_transcript"]}
        result["qa"] = []
        if result["has_qa"]:
            saved = read_json(path / "qa.json")
            result["qa"] = saved["qa"]
            result["transcript"]["segments"] = models.transcript_roles(segments, saved["qa"])
            result["question_snapshot"] = saved.get("question_snapshot", {})
            result["unassigned_segment_ids"] = saved.get("unassigned_segment_ids", [])
    return result


def list_records(job_id):
    root = interview_root(job_id)
    records = [public_record(read_json(path), path.parent) for path in root.glob("*/state.json")]
    return sorted(records, key=lambda row: row["created_at"], reverse=True)


async def store_stream(job_id, filename, read):
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS:
        raise HTTPException(400, "Choose an MP4, WebM, MOV, M4V or MKV video.")
    root = interview_root(job_id)
    root.mkdir(exist_ok=True)
    temporary = root / f".upload-{uuid.uuid4().hex}{extension}"
    digest, total = hashlib.sha256(), 0
    try:
        with temporary.open("wb") as target:
            while data := await read(1024 * 1024):
                total += len(data)
                if total > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "The video must be smaller than 2 GB.")
                digest.update(data)
                target.write(data)
        if not total:
            raise HTTPException(400, "The video is empty.")
        checksum = digest.hexdigest()
        async with IMPORT_LOCKS.setdefault(job_id, asyncio.Lock()):
            # Protect against a concurrent candidate deletion while upload was streaming.
            main_module().folder(job_id)
            for existing in root.glob("*/state.json"):
                record = read_json(existing)
                if record.get("sha256") == checksum:
                    return public_record(record, existing.parent, detail=True)
            try:
                duration = await media.probe(temporary)
            except media.MediaError as error:
                raise HTTPException(400, str(error)) from None
            interview_id = uuid.uuid4().hex
            path = root / interview_id
            path.mkdir()
            video_file = "video" + extension
            temporary.replace(path / video_file)
            record = {"id": interview_id, "candidate_id": job_id,
                      "name": Path(filename.replace("\\", "/")).name[:250], "size": total,
                      "sha256": checksum, "video_file": video_file, "duration": duration,
                      "status": "ready", "stage": "uploaded", "error": None,
                      "progress": {"completed": 0, "total": 0, "percent": 0},
                      "created_at": now(), "updated_at": now(), "receipts": []}
            write_json(path / "state.json", record)
            return public_record(record, path, detail=True)
    finally:
        temporary.unlink(missing_ok=True)


class SampleInput(BaseModel):
    name: str = Field(min_length=1, max_length=250)


class ProcessInput(BaseModel):
    retry: bool = False


@router.get("/api/interviews/samples")
async def list_samples():
    return [{"name": path.name, "size": path.stat().st_size} for path in samples()]


@router.get("/api/profiles/{job_id}/interviews")
async def list_interviews(job_id: str):
    return list_records(job_id)


@router.post("/api/profiles/{job_id}/interviews", status_code=201)
async def upload_video(job_id: str, file: UploadFile = File(...)):
    try:
        async with main_module().lock(job_id):
            main_module().ensure_idle(main_module().read_job(job_id))
            return await store_stream(job_id, file.filename or "video.mp4", file.read)
    finally:
        await file.close()


@router.post("/api/profiles/{job_id}/interviews/sample", status_code=201)
async def import_sample(job_id: str, body: SampleInput):
    path = next((path for path in samples() if path.name == body.name), None)
    if path is None:
        raise HTTPException(404, "Local video not found.")
    async with main_module().lock(job_id):
        main_module().ensure_idle(main_module().read_job(job_id))
        with path.open("rb") as source:
            async def read(size):
                return await asyncio.to_thread(source.read, size)
            return await store_stream(job_id, path.name, read)


@router.get("/api/profiles/{job_id}/interviews/{interview_id}")
async def get_interview(job_id: str, interview_id: str):
    path = directory(job_id, interview_id)
    return public_record(read_json(path / "state.json"), path, detail=True)


def current_snapshot(job_id):
    from .questions import current_snapshot as questions_snapshot
    return questions_snapshot(job_id)


def add_receipt(path, receipt, stage, chunk_index=None):
    record = read_json(path / "state.json")
    receipts = record.get("receipts", [])
    receipts.append({"stage": stage, "chunk": chunk_index, "created_at": now(), **(receipt or {})})
    update_record(path, receipts=receipts)


def transcript_text(segments):
    def clock(seconds):
        value = int(seconds)
        return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"
    labels = {"question": "Question", "answer": "Answer", "statement": "Statement"}
    return "\n\n".join(f"[{clock(s['start'])}–{clock(s['end'])}] {labels.get(s.get('qa_role'), 'Speech')}\n{s['text']}" for s in segments)


def chunk_concurrency():
    try:
        return max(1, min(10, int(os.getenv("QWEN_CHUNK_CONCURRENCY", "10"))))
    except ValueError:
        return 10


def archive_chunk_attempt(path, index):
    """Retain prior failed output before an explicitly requested retry."""
    root = path / "chunks"
    previous = [root / f"{index:04d}{suffix}" for suffix in
                (".response.txt", ".partial.txt", ".partial.receipt.json")]
    previous = [source for source in previous if source.is_file()]
    if previous:
        archive = root / "attempts" / f"{index:04d}-{uuid.uuid4().hex}"
        archive.mkdir(parents=True)
        for source in previous:
            shutil.copyfile(source, archive / source.name)


async def transcribe_pending(path, chunks):
    """Save independent results; one failed request must not discard its peers."""
    def checkpoint(chunk):
        return path / "chunks" / f"{chunk['index']:04d}.json"

    def progress():
        done = sum(checkpoint(chunk).is_file() for chunk in chunks)
        return {"completed": done, "total": len(chunks),
                "percent": round(done / len(chunks) * 90) if chunks else 90}

    pending = [chunk for chunk in chunks if not checkpoint(chunk).is_file()]
    if not pending:
        # Preserve the last measured paid batch when resume only assembles saved
        # transcripts and runs Q&A, with no new transcription requests.
        update_record(path, stage="transcribing", progress=progress(), chunk_errors={}, failed_chunk_indices=[])
        return
    batch_start = time.monotonic()
    update_record(path, stage="transcribing", progress=progress(), chunk_errors={}, failed_chunk_indices=[],
                  batch_started_at=now(), batch_elapsed_seconds=None)
    limit = asyncio.Semaphore(chunk_concurrency())

    async def transcribe_one(chunk):
        async with limit:
            call_start, started_at = None, None
            try:
                archive_chunk_attempt(path, chunk["index"])
                started_at, call_start = now(), time.monotonic()
                content, receipt = await models.transcribe_chunk(path / "chunks" / chunk["filename"], chunk["duration"])
                receipt = {**receipt, "started_at": started_at,
                           "elapsed_seconds": round(time.monotonic() - call_start, 3)}
                # These synchronous atomic writes run on one event loop, so another
                # completed chunk cannot interleave a stale read-modify-write.
                add_receipt(path, receipt, "transcription", chunk["index"])
                (path / "chunks" / f"{chunk['index']:04d}.response.txt").write_text(content, encoding="utf-8")
                try:
                    normalizations = []
                    segments = models.validate_transcript(content, chunk, audit=normalizations)
                except (ValueError, KeyError, TypeError) as error:
                    raise ai.AIError(f"Transcript validation failed: {error}.") from None
                write_json(checkpoint(chunk), {"segments": segments, "chunk": chunk, "receipt": receipt,
                                               "normalizations": normalizations})
            except Exception as error:
                if isinstance(error, ai.AIError) and error.usage:
                    receipt = {**error.usage, "started_at": started_at,
                               "elapsed_seconds": round(time.monotonic() - call_start, 3) if call_start is not None else None}
                    add_receipt(path, receipt, "transcription", chunk["index"])
                message = str(error) if isinstance(error, ai.AIError) else "Transcription processing failed."
                errors = read_json(path / "state.json").get("chunk_errors", {})
                errors[str(chunk["index"])] = message
                update_record(path, chunk_errors=errors, failed_chunk_indices=sorted(int(index) for index in errors))
                raise ai.AIError(message) from None
            finally:
                update_record(path, progress=progress())

    tasks = [asyncio.create_task(transcribe_one(chunk), name=f"interview-chunk-{chunk['index']}") for chunk in pending]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # Covers explicit Cancel and shutdown, including workers queued on the limit.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        update_record(path, batch_elapsed_seconds=round(time.monotonic() - batch_start, 3))
    failed = [(chunk, result) for chunk, result in zip(pending, results) if isinstance(result, BaseException)]
    if failed:
        details = "; ".join(f"{chunk['index'] + 1}: {error}" for chunk, error in failed)
        raise ai.AIError(f"Transcription failed for chunks {details} Resume retries only missing chunks.")


async def run_pipeline(job_id, interview_id):
    path = directory(job_id, interview_id)
    try:
        async with PROCESS_LIMIT:
            record = read_json(path / "state.json")
            audio = path / "audio.wav"
            if not audio.is_file():
                update_record(path, stage="extracting")
                await media.extract_audio(path / record["video_file"], audio)
            chunks = await asyncio.to_thread(media.chunk_audio, audio, path / "chunks")
            update_record(path, chunks=chunks)
            if not (path / "transcript.json").is_file():
                await transcribe_pending(path, chunks)
                segments = available_segments(path)
                write_json(path / "transcript.json", {"model": models.QWEN_MODEL, "segments": segments,
                                                       "created_at": now(), "duration": record["duration"]})
                (path / "transcript.txt").write_text(transcript_text(segments), encoding="utf-8")
            segments = read_json(path / "transcript.json")["segments"]
            if not (path / "qa.json").is_file():
                update_record(path, stage="qa", progress={"completed": len(chunks), "total": len(chunks), "percent": 95})
                snapshot = current_snapshot(job_id)
                if segments:
                    content, receipt = await models.group_qa(segments, snapshot)
                    add_receipt(path, receipt, "qa")
                    (path / "qa.response.txt").write_text(content, encoding="utf-8")
                    try:
                        result = models.validate_qa(content, segments, snapshot)
                    except (ValueError, TypeError, KeyError) as error:
                        raise ai.AIError(f"Question-answer grouping failed validation: {error}. Resume retries Q&A only.") from None
                else:
                    result = {"qa": [], "unassigned_segment_ids": []}
                write_json(path / "qa.json", {**result, "question_snapshot": snapshot, "created_at": now()})
            update_record(path, status="completed", stage="completed", error=None,
                          progress={"completed": len(chunks), "total": len(chunks), "percent": 100})
    except asyncio.CancelledError:
        if (path / "state.json").exists():
            update_record(path, status="cancelled", error="Processing stopped. Completed stages are saved.")
        raise
    except (ai.AIError, media.MediaError) as error:
        if getattr(error, "usage", None):
            add_receipt(path, error.usage, read_json(path / "state.json").get("stage", "unknown"))
        update_record(path, status="failed", error=str(error))
    except Exception:
        # Do not surface local paths, credentials or provider payloads to the client.
        update_record(path, status="failed", error="Interview processing failed. Saved stages remain available; resume explicitly.")
    finally:
        TASKS.pop((job_id, interview_id), None)


@router.post("/api/profiles/{job_id}/interviews/{interview_id}/process", status_code=202)
async def process_interview(job_id: str, interview_id: str, body: ProcessInput = ProcessInput()):
    async with main_module().lock(job_id):
        path = directory(job_id, interview_id)
        record = read_json(path / "state.json")
        if record["status"] == "completed" and (path / "qa.json").is_file():
            return public_record(record, path, detail=True)
        if is_busy(job_id) or record["status"] == "processing":
            raise HTTPException(409, "An interview is already processing.")
        main_module().ensure_idle(main_module().read_job(job_id))
        if record["status"] in {"failed", "interrupted", "cancelled"} and not body.retry:
            raise HTTPException(409, "Resume explicitly to retry the unfinished stage.")
        # Fail configuration checks before scheduling a paid request.
        try:
            if not (path / "transcript.json").is_file():
                models.qwen_config()
            if not (path / "qa.json").is_file():
                ai.read_api_key()
            if not (path / "audio.wav").is_file():
                media.executable("ffmpeg")
        except (ai.AIError, media.MediaError) as error:
            raise HTTPException(400, str(error)) from None
        record = update_record(path, status="processing", error=None)
        task = asyncio.create_task(run_pipeline(job_id, interview_id))
        TASKS[(job_id, interview_id)] = task
        return public_record(record, path, detail=True)


@router.post("/api/profiles/{job_id}/interviews/{interview_id}/cancel")
async def cancel_interview(job_id: str, interview_id: str):
    directory(job_id, interview_id)
    task = TASKS.get((job_id, interview_id))
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        TASKS.pop((job_id, interview_id), None)
        path = directory(job_id, interview_id)
        if read_json(path / "state.json")["status"] == "processing":
            update_record(path, status="cancelled", error="Processing stopped. Completed stages are saved.")
    return await get_interview(job_id, interview_id)


@router.get("/api/profiles/{job_id}/interviews/{interview_id}/video")
async def get_video(job_id: str, interview_id: str):
    path = directory(job_id, interview_id)
    record = read_json(path / "state.json")
    return FileResponse(path / record["video_file"], filename=record["name"],
                        media_type=mimetypes.guess_type(record["video_file"])[0] or "video/mp4",
                        content_disposition_type="inline")


@router.get("/api/profiles/{job_id}/interviews/{interview_id}/audio")
async def get_audio(job_id: str, interview_id: str):
    path = directory(job_id, interview_id) / "audio.wav"
    if not path.is_file():
        raise HTTPException(404, "Audio is not available yet.")
    return FileResponse(path, media_type="audio/wav", filename="interview-audio.wav", content_disposition_type="inline")


@router.get("/api/profiles/{job_id}/interviews/{interview_id}/transcript.txt")
async def get_transcript_text(job_id: str, interview_id: str):
    path = directory(job_id, interview_id)
    segments = available_segments(path)
    if not segments and not (path / "transcript.json").exists():
        raise HTTPException(404, "Transcript is not available yet.")
    if (path / "qa.json").is_file():
        segments = models.transcript_roles(segments, read_json(path / "qa.json")["qa"])
    return Response(transcript_text(segments), media_type="text/plain",
                    headers={"Content-Disposition": 'attachment; filename="interview-transcript.txt"'})


async def startup():
    """Mark interrupted work; never resume a paid stage automatically."""
    global PROCESS_LIMIT
    PROCESS_LIMIT = asyncio.Semaphore(1)
    for path in main_module().STORAGE.glob("*/interviews/*/state.json"):
        record = read_json(path)
        if record.get("status") == "processing":
            update_record(path.parent, status="interrupted", error="Processing was interrupted. Resume the unfinished stage explicitly.")


async def shutdown():
    entries = list(TASKS.items())
    tasks = [task for _, task in entries]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    TASKS.clear()
    for (job_id, interview_id), _ in entries:
        try:
            path = directory(job_id, interview_id)
            if read_json(path / "state.json").get("status") == "processing":
                update_record(path, status="interrupted", error="Processing was interrupted. Resume explicitly.")
        except HTTPException:
            pass
