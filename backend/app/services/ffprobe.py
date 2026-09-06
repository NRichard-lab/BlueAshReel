from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class FFprobeError(RuntimeError):
    pass


def _integer(value: Any) -> int | None:
    try:
        result = int(value)
        return result if result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _rate(value: Any) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        numerator, denominator = str(value).split("/", 1)
        result = float(numerator) / float(denominator)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class ProbeStream:
    stream_index: int
    codec: str | None
    language: str | None
    title: str | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    container: str | None
    duration_seconds: float | None
    bitrate: int | None
    embedded_title: str | None
    video: tuple[ProbeStream, ...]
    audio: tuple[ProbeStream, ...]
    subtitles: tuple[ProbeStream, ...]


def parse_ffprobe_output(payload: dict[str, Any]) -> ProbeResult:
    raw_format = payload.get("format")
    format_info: dict[str, Any] = raw_format if isinstance(raw_format, dict) else {}
    raw_tags = format_info.get("tags")
    tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
    collections: dict[str, list[ProbeStream]] = {"video": [], "audio": [], "subtitle": []}
    streams_value = payload.get("streams")
    raw_streams: list[Any] = streams_value if isinstance(streams_value, list) else []
    if len(raw_streams) > 128:
        raise FFprobeError("Media exceeds the supported local stream-count limit")
    for stream_value in raw_streams:
        if not isinstance(stream_value, dict):
            continue
        raw: dict[str, Any] = stream_value
        if raw.get("codec_type") not in collections:
            continue
        stream_tags_value = raw.get("tags")
        stream_tags: dict[str, Any] = stream_tags_value if isinstance(stream_tags_value, dict) else {}
        disposition_value = raw.get("disposition")
        disposition: dict[str, Any] = disposition_value if isinstance(disposition_value, dict) else {}
        kind = str(raw["codec_type"])
        if kind == "video" and disposition.get("attached_pic"):
            continue
        common = ProbeStream(
            stream_index=_integer(raw.get("index")) or 0,
            codec=str(raw["codec_name"])[:80] if raw.get("codec_name") else None,
            language=str(stream_tags["language"])[:32] if stream_tags.get("language") else None,
            title=str(stream_tags["title"])[:200] if stream_tags.get("title") else None,
            details={
                "width": _integer(raw.get("width")),
                "height": _integer(raw.get("height")),
                "bitrate": _integer(raw.get("bit_rate")),
                "frame_rate": _rate(raw.get("avg_frame_rate")),
                "channels": _integer(raw.get("channels")),
                "channel_layout": str(raw["channel_layout"])[:80] if raw.get("channel_layout") else None,
                "forced": bool(disposition.get("forced", 0)),
                "hearing_impaired": bool(disposition.get("hearing_impaired", 0)),
                "profile": str(raw["profile"])[:80] if raw.get("profile") else None,
                "level": _integer(raw.get("level")),
                "pixel_format": str(raw["pix_fmt"])[:40] if raw.get("pix_fmt") else None,
                "bit_depth": _integer(raw.get("bits_per_raw_sample")),
            },
        )
        collections[kind].append(common)
    return ProbeResult(
        container=str(format_info["format_name"])[:120] if format_info.get("format_name") else None,
        duration_seconds=_number(format_info.get("duration")),
        bitrate=_integer(format_info.get("bit_rate")),
        embedded_title=str(tags["title"])[:500] if tags.get("title") else None,
        video=tuple(collections["video"]),
        audio=tuple(collections["audio"]),
        subtitles=tuple(collections["subtitle"]),
    )


def run_ffprobe(executable: str, path: Path, timeout_seconds: int) -> tuple[ProbeResult, int]:
    started = time.monotonic()
    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-protocol_whitelist",
        "file,pipe",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFprobeError("Local media analysis could not be completed") from exc
    if completed.returncode != 0:
        raise FFprobeError("Local media analysis reported an invalid or unsupported file")
    if len(completed.stdout) > 10 * 1024 * 1024:
        raise FFprobeError("Local media analysis output exceeded the safety limit")
    try:
        raw = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FFprobeError("Local media analysis returned invalid data") from exc
    if not isinstance(raw, dict):
        raise FFprobeError("Local media analysis returned invalid data")
    return parse_ffprobe_output(raw), int((time.monotonic() - started) * 1000)


def local_binary_available(executable: str) -> bool:
    try:
        result = subprocess.run([executable, "-version"], capture_output=True, check=False, timeout=5, text=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ffprobe_available(executable: str) -> bool:
    return local_binary_available(executable)


def ffmpeg_available(executable: str) -> bool:
    return local_binary_available(executable)
