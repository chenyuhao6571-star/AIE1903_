"""Read-only local video samples for the Project 2 UI prototype."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/interview-demo")
VIDEO_DIR = Path(__file__).resolve().parents[2] / "data"


def available_videos():
    root = VIDEO_DIR.resolve()
    return sorted(
        (path for path in root.glob("*.mp4")
         if path.is_file() and path.resolve().parent == root),
        key=lambda path: ("screening" not in path.name.lower(), path.name.lower()),
    )


@router.get("/videos")
async def videos():
    return [{"name": path.name, "size": path.stat().st_size} for path in available_videos()]


@router.get("/videos/{filename}")
async def video(filename: str):
    path = next((path for path in available_videos() if path.name == filename), None)
    if path is None:
        raise HTTPException(404, "Sample video not found.")
    return FileResponse(path, media_type="video/mp4", filename=path.name,
                        content_disposition_type="inline")
