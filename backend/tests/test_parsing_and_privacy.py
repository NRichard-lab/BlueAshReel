from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from app.config import AppConfig
from app.logging_config import JsonFormatter, redact
from app.services.ffprobe import parse_ffprobe_output
from app.services.filename_parser import parse_filename
from app.services.paths import UnsafeMediaPath, validate_media_directory


def test_filename_parsing_common_tv_and_movie_patterns() -> None:
    tv = parse_filename("Example.Show.S01E02.Pilot.mkv", "tv")
    alt = parse_filename("Example Show 2x03 Second Part.mp4", "tv")
    movie = parse_filename("A.Film.2024.1080p.BluRay.x264.mkv", "movies")
    uncertain = parse_filename("family_clip_001.mov", "other")
    quality_only = parse_filename("Show.S01E01.1080p.WEB-DL.mkv", "tv")
    named_quality = parse_filename("Show.S01E02.Real.Title.1080p.WEB-DL.mkv", "tv")

    assert (tv.series_title, tv.season_number, tv.episode_number, tv.episode_title) == (
        "Example Show",
        1,
        2,
        "Pilot",
    )
    assert (alt.season_number, alt.episode_number) == (2, 3)
    assert (movie.title, movie.year, movie.kind) == ("A Film", 2024, "movie")
    assert uncertain.kind == "other" and uncertain.confidence < 0.5
    assert (quality_only.title, quality_only.episode_title) == ("Episode 1", None)
    assert (named_quality.title, named_quality.episode_title) == ("Real Title", "Real Title")


def test_ffprobe_fixture_parses_stream_details() -> None:
    fixture = Path(__file__).parent / "fixtures" / "ffprobe.json"
    result = parse_ffprobe_output(json.loads(fixture.read_text(encoding="utf-8")))
    assert result.container == "matroska,webm"
    assert result.duration_seconds == 3661.25
    assert result.video[0].details["width"] == 1920
    assert round(result.video[0].details["frame_rate"], 3) == 23.976
    assert result.audio[0].language == "eng"
    assert result.subtitles[0].details["forced"] is True


def test_ffprobe_parser_tolerates_invalid_optional_values() -> None:
    parsed = parse_ffprobe_output(
        {
            "format": {"duration": "NaN", "bit_rate": "invalid"},
            "streams": [{"index": "x", "codec_type": "video", "avg_frame_rate": "1/0"}],
        }
    )
    assert parsed.duration_seconds is None
    assert parsed.bitrate is None
    assert parsed.video[0].stream_index == 0
    assert parsed.video[0].details["frame_rate"] is None


def test_structured_logs_and_audit_redaction_hide_secrets() -> None:
    assert redact({"password": "secret", "token": "abc", "safe": "ok"}) == {
        "password": "[REDACTED]",
        "token": "[REDACTED]",
        "safe": "ok",
    }
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("privacy-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("login token=supersecret", extra={"fields": {"password": "do-not-log", "count": 2}})
    output = stream.getvalue()
    assert "supersecret" not in output
    assert "do-not-log" not in output
    assert "[REDACTED]" in output


def test_path_validation_fails_closed_without_configured_media_roots(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    config = AppConfig(
        app_data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        artwork_dir=tmp_path / "artwork",
        media_roots="",
    )
    with pytest.raises(UnsafeMediaPath, match="No media roots"):
        validate_media_directory(str(media), config)
