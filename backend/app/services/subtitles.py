from __future__ import annotations

import html
import re
import subprocess
import threading
from pathlib import Path

from fastapi import HTTPException

from app.config import AppConfig

_TIMING = re.compile(r"^((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})(?:\s.*)?$")
_SUBTITLE_SLOTS = threading.BoundedSemaphore(2)


def timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
    return hours * 3600 + minutes * 60 + seconds if minutes < 60 and seconds < 60 else -1


def sanitize_vtt(source: str) -> str:
    if len(source) > 2 * 1024 * 1024:
        raise HTTPException(422, "Subtitle text exceeds the local safety limit")
    cues: list[str] = []
    lines = source.replace("\r", "").split("\n")
    index = 0
    while index < len(lines):
        match = _TIMING.match(lines[index].strip())
        index += 1
        if match is None:
            continue
        if timestamp(match[1]) < 0 or timestamp(match[2]) <= timestamp(match[1]):
            continue
        timing = match[1].replace(",", ".") + " --> " + match[2].replace(",", ".")
        content: list[str] = []
        while index < len(lines) and lines[index].strip():
            plain = re.sub(r"<[^>]*>", "", html.unescape(lines[index]))
            content.append(html.escape("".join(c for c in plain if c.isprintable())))
            index += 1
        cues.append(timing + "\n" + "\n".join(content))
        if len(cues) > 20000:
            raise HTTPException(422, "Subtitle cue count exceeds the local safety limit")
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"


def extract_subtitles(source: Path, stream_index: int, config: AppConfig) -> str:
    command = [
        config.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        "mov,matroska,webm,avi,asf,mpegts,mpeg",
        "-i",
        str(source),
        "-map",
        f"0:{stream_index}",
        "-c:s",
        "webvtt",
        "-f",
        "webvtt",
        "-fs",
        "2097152",
        "pipe:1",
    ]
    if not _SUBTITLE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Local subtitle conversion is busy. Try again shortly.")
    try:
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            output = bytearray()
            exceeded = threading.Event()
            assert process.stdout is not None

            def read_bounded() -> None:
                assert process.stdout is not None
                while block := process.stdout.read(65536):
                    if len(output) + len(block) > 2 * 1024 * 1024:
                        exceeded.set()
                        process.kill()
                        break
                    output.extend(block)

            reader = threading.Thread(target=read_bounded, daemon=True)
            reader.start()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise HTTPException(422, "Local subtitle conversion timed out") from None
            finally:
                reader.join(timeout=5)
            if process.returncode != 0 or exceeded.is_set():
                raise HTTPException(422, "This subtitle track cannot be converted within local safety limits")
            return sanitize_vtt(output.decode("utf-8", errors="replace"))
    except OSError as exc:
        raise HTTPException(422, "Local subtitle conversion could not start") from exc
    finally:
        _SUBTITLE_SLOTS.release()
