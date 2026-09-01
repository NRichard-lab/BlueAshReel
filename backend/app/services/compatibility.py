"""Versioned local playback policy. Client capability reports are hints, not trust."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import AppConfig
from app.models import MediaFile

RULES_VERSION = 1
TEXT_SUBTITLES = frozenset({"subrip", "srt", "webvtt", "ass", "ssa", "mov_text"})
IMAGE_SUBTITLES = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"})


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")
    h264: bool = False
    aac: bool = False
    webm_vp9: bool = False
    opus: bool = False
    hls: bool = False
    max_height: int = Field(default=2160, ge=240, le=4320)
    max_h264_level: int = Field(default=51, ge=30, le=62)


class PlaybackChoice(BaseModel):
    file_id: str = Field(min_length=1, max_length=36)
    capabilities: Capabilities
    audio_index: int | None = Field(default=None, ge=0, le=1000)
    subtitle_index: int | None = Field(default=None, ge=0, le=1000)
    quality: Literal["original", "1080p", "720p", "480p"] = "original"
    restart: bool = False
    position_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Decision(BaseModel):
    version: int = RULES_VERSION
    method: Literal["direct", "remux", "transcode", "unsupported"]
    reason: str
    video_copy: bool = False
    audio_copy: bool = False
    output_height: int = 0
    bitrate_kbps: int = 0
    burn_subtitle: bool = False
    mime: str = "video/mp4"


def decide(file: MediaFile, choice: PlaybackChoice, config: AppConfig) -> Decision:
    def unsupported(reason: str) -> Decision:
        return Decision(method="unsupported", reason=reason)

    videos = sorted(file.video_streams, key=lambda stream: stream.stream_index)
    audios = sorted(file.audio_streams, key=lambda stream: stream.stream_index)
    if not videos or not file.duration_seconds or file.duration_seconds <= 0:
        return unsupported("A video stream and a known duration are required. Rescan this file.")
    video = videos[0]
    audio = next((a for a in audios if a.stream_index == choice.audio_index), audios[0] if audios else None)
    if choice.audio_index is not None and (audio is None or audio.stream_index != choice.audio_index):
        return unsupported("The selected audio track is unavailable.")
    subtitle = next((s for s in file.subtitle_streams if s.stream_index == choice.subtitle_index), None)
    if choice.subtitle_index is not None and subtitle is None:
        return unsupported("The selected subtitle track is unavailable.")
    if subtitle and subtitle.codec not in TEXT_SUBTITLES | IMAGE_SUBTITLES:
        return unsupported("This subtitle format is not supported by local playback.")
    if subtitle and subtitle.codec in IMAGE_SUBTITLES:
        return unsupported(
            "Image subtitle burn-in is not supported by this build. Choose a text track or turn subtitles off."
        )
    caps = choice.capabilities
    quality_height, quality_bitrate = {
        "original": (config.transcode_max_height, config.transcode_max_bitrate_kbps),
        "1080p": (1080, 6000),
        "720p": (720, 3000),
        "480p": (480, 1200),
    }[choice.quality]
    height = min(video.height or 1080, quality_height, caps.max_height, config.transcode_max_height)
    height = max(2, height // 2 * 2)
    bitrate = min(quality_bitrate, config.transcode_max_bitrate_kbps)
    reduce = (video.height or 0) > height or bool(file.bitrate and file.bitrate > bitrate * 1000)
    burn = bool(subtitle and subtitle.codec in IMAGE_SUBTITLES)
    h264 = (
        caps.h264
        and video.codec == "h264"
        and video.pixel_format in ("yuv420p", "yuvj420p")
        and (video.bit_depth or 8) <= 8
        and video.profile in ("Constrained Baseline", "Baseline", "Main", "High")
        and video.level is not None
        and video.level <= caps.max_h264_level
    )
    aac = audio is None or (caps.aac and audio.codec == "aac" and (audio.channels or 2) <= 2)
    # Browser APIs do not reliably select embedded tracks or expose disposition.
    default_audio = len(audios) <= 1
    container = set((file.container or "").split(","))
    if h264 and aac and not reduce and not burn and default_audio and container.intersection({"mov", "mp4"}):
        return Decision(
            method="direct",
            reason="Compatible MP4 video and audio; decoding is verified by the player.",
            video_copy=True,
            audio_copy=True,
            output_height=video.height or 0,
            bitrate_kbps=(file.bitrate or 0) // 1000,
        )
    if (
        caps.webm_vp9
        and caps.opus
        and video.codec == "vp9"
        and (video.bit_depth or 8) <= 8
        and video.pixel_format == "yuv420p"
        and video.profile == "Profile 0"
        and (audio is None or audio.codec == "opus")
        and default_audio
        and "webm" in container
        and PurePosixPath(file.relative_path).suffix.lower() == ".webm"
        and not reduce
        and not burn
    ):
        return Decision(
            method="direct",
            reason="Compatible WebM video and audio.",
            video_copy=True,
            audio_copy=True,
            output_height=height,
            mime="video/webm",
        )
    if not (caps.hls and caps.h264 and caps.aac):
        return unsupported("This browser cannot play the source or the local H.264/AAC streaming output.")
    video_copy = h264 and not reduce and not burn
    method = "remux" if video_copy and aac else "transcode"
    return Decision(
        method=method,
        reason="Local container/audio-track remapping."
        if method == "remux"
        else "Local conversion is required by the selected video, audio, subtitle, or quality settings.",
        video_copy=video_copy,
        audio_copy=aac,
        output_height=height,
        bitrate_kbps=bitrate,
        burn_subtitle=burn,
        mime="application/vnd.apple.mpegurl",
    )
