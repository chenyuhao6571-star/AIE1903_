"""Local audio extraction and bounded WAV chunks. No network calls."""

import asyncio
import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path


class MediaError(Exception):
    pass


def executable(name):
    configured = os.getenv(f"{name.upper()}_PATH")
    value = configured or shutil.which(name)
    if not value or not Path(value).is_file():
        raise MediaError(f"{name} is unavailable. Install FFmpeg or configure {name.upper()}_PATH.")
    return value


async def command(*args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE, creationflags=flags)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise MediaError("The video could not be decoded. Use a video with an audio track supported by FFmpeg.")
    return stdout


async def probe(path):
    raw = await command(executable("ffprobe"), "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type", "-of", "json", str(path))
    try:
        data = json.loads(raw)
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0 or duration > 7200:
            raise ValueError
        if not any(stream.get("codec_type") == "audio" for stream in data["streams"]):
            raise MediaError("The video has no audio track.")
        if not any(stream.get("codec_type") == "video" for stream in data["streams"]):
            raise MediaError("Choose a video file with a video track.")
        return duration
    except (ValueError, KeyError, TypeError):
        raise MediaError("The video duration is invalid or exceeds two hours.") from None


async def extract_audio(video_path, audio_path):
    target = audio_path.with_name("audio.partial.wav")
    try:
        await command(executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                      "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
                      "-c:a", "pcm_s16le", str(target))
        with wave.open(str(target), "rb") as source:
            duration = source.getnframes() / source.getframerate()
            if duration <= 0:
                raise MediaError("The extracted audio is empty.")
        target.replace(audio_path)
        return duration
    finally:
        target.unlink(missing_ok=True)


def chunk_audio(audio_path, chunk_dir, seconds=180):
    """180s mono 16k PCM is ~7.7 MB base64, below DashScope's 10 MB limit."""
    chunk_dir.mkdir(exist_ok=True)
    chunks = []
    with wave.open(str(audio_path), "rb") as source:
        rate = source.getframerate()
        frames_per_chunk = rate * seconds
        total = source.getnframes()
        for index, offset in enumerate(range(0, total, frames_per_chunk)):
            frames = min(frames_per_chunk, total - offset)
            path = chunk_dir / f"{index:04d}.wav"
            if not path.exists():
                source.setpos(offset)
                data = source.readframes(frames)
                partial = path.with_suffix(".partial.wav")
                with wave.open(str(partial), "wb") as out:
                    out.setparams(source.getparams())
                    out.writeframes(data)
                partial.replace(path)
            chunks.append({"index": index, "start": offset / rate, "duration": frames / rate,
                           "filename": path.name})
    return chunks
