"""Versioned local playback policy. Client capability reports are hints, not trust."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import AppConfig
from app.models import MediaFile
from app.services.transcoding_policy import TranscodingPolicy, default_policy

RULES_VERSION = 3
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
    hevc: bool = False
    hevc_10bit: bool = False
    av1: bool = False
    vp9_10bit: bool = False
    ac3: bool = False
    eac3: bool = False
    dts: bool = False
    truehd: bool = False
    flac: bool = False
    matroska: bool = False
    multi_audio: bool = False
    embedded_track_selection: bool = False
    multichannel_aac: bool = False
    aac_multichannel: bool = False
    image_subtitles: bool = False
    max_audio_channels: int = Field(default=2, ge=1, le=16)
    max_hevc_level: int = Field(default=153, ge=30, le=255)


class PlaybackChoice(BaseModel):
    file_id: str = Field(min_length=1, max_length=36)
    capabilities: Capabilities
    audio_index: int | None = Field(default=None, ge=0, le=1000)
    subtitle_index: int | None = Field(default=None, ge=0, le=1000)
    quality: Literal["original", "1080p", "720p", "480p"] = "original"
    restart: bool = False
    position_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    recovery_from: str | None = Field(default=None, min_length=1, max_length=36)
    delivery: Literal["auto", "remux", "transcode"] = "auto"


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
    audio_transcode: bool = False
    direct_play_blockers: list[str] = Field(default_factory=list)


def decide(
    file: MediaFile, choice: PlaybackChoice, config: AppConfig, policy: TranscodingPolicy | None = None
) -> Decision:
    policy = policy or default_policy(config)

    def unsupported(reason: str, blockers: list[str] | None = None) -> Decision:
        return Decision(method="unsupported", reason=reason, direct_play_blockers=blockers or [])

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
    caps = choice.capabilities
    if subtitle and subtitle.codec in IMAGE_SUBTITLES and not caps.image_subtitles:
        return unsupported(
            "Image subtitle burn-in is not supported by this build. Choose a text track or turn subtitles off.",
            ["subtitle_format"],
        )
    quality_height, quality_bitrate = {
        "original": (policy.max_height, policy.max_bitrate_kbps),
        "1080p": (1080, 6000),
        "720p": (720, 3000),
        "480p": (480, 1200),
    }[choice.quality]
    height = min(video.height or 1080, quality_height, caps.max_height, policy.max_height)
    height = max(2, height // 2 * 2)
    bitrate = min(quality_bitrate, policy.max_bitrate_kbps)
    reduce = (video.height or 0) > height or bool(file.bitrate and file.bitrate > bitrate * 1000)
    burn = False
    h264 = (
        caps.h264
        and video.codec == "h264"
        and video.pixel_format in ("yuv420p", "yuvj420p")
        and (video.bit_depth or 8) <= 8
        and video.profile in ("Constrained Baseline", "Baseline", "Main", "High")
        and video.level is not None
        and video.level <= caps.max_h264_level
    )
    bit_depth = video.bit_depth or 8
    hevc = (
        caps.hevc
        and video.codec in {"hevc", "h265"}
        and video.pixel_format in {"yuv420p", "yuv420p10le"}
        and bit_depth <= (10 if caps.hevc_10bit else 8)
        and video.level is not None
        and video.level <= caps.max_hevc_level
    )
    av1 = caps.av1 and video.codec == "av1" and bit_depth <= 10 and video.profile in {None, "Main"}
    vp9 = (
        caps.webm_vp9 and video.codec == "vp9" and video.pixel_format in {"yuv420p", "yuv420p10le"}
        and bit_depth <= (10 if caps.vp9_10bit else 8) and video.profile in {"Profile 0", "Profile 2"}
    )
    video_ok = h264 or hevc or av1 or vp9
    channels = audio.channels or 2 if audio else 0
    audio_codec_ok = audio is None or {
        "aac": caps.aac and (channels <= 2 or caps.multichannel_aac or caps.aac_multichannel),
        "ac3": caps.ac3,
        "eac3": caps.eac3,
        "dts": caps.dts,
        "truehd": caps.truehd,
        "flac": caps.flac,
        "opus": caps.opus,
    }.get(audio.codec or "", False)
    audio_ok = audio_codec_ok and channels <= caps.max_audio_channels
    # Browser APIs do not reliably select embedded tracks or expose disposition.
    default_audio = len(audios) <= 1 or caps.multi_audio or caps.embedded_track_selection
    container = set((file.container or "").split(","))
    is_matroska = bool(container.intersection({"matroska", "mkv"}))
    container_ok = bool(container.intersection({"mov", "mp4"})) or (caps.matroska and is_matroska)
    blockers: list[str] = []
    if not video_ok:
        blockers.append("video_codec")
    if not audio_codec_ok:
        blockers.append("audio_codec")
    elif channels > caps.max_audio_channels:
        blockers.append("audio_channels")
    if not default_audio:
        blockers.append("multiple_audio_tracks")
    if not container_ok:
        blockers.append("container")
    if reduce:
        blockers.append("quality_reduction")
    if (
        choice.delivery == "auto" and video_ok and audio_ok and not reduce and not burn
        and default_audio and container_ok
    ):
        mime = "video/x-matroska" if is_matroska else "video/mp4"
        return Decision(
            method="direct",
            reason="Compatible source video, audio, and container; decoding is verified by the player.",
            video_copy=True,
            audio_copy=True,
            output_height=video.height or 0,
            bitrate_kbps=(file.bitrate or 0) // 1000,
            mime=mime,
        )
    if (
        caps.webm_vp9
        and choice.delivery == "auto"
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
        return unsupported("This browser cannot play the source or the local H.264/AAC streaming output.", blockers)
    video_copy = h264 and not reduce and not burn and choice.delivery != "transcode"
    legacy_aac = audio is None or (caps.aac and audio.codec == "aac" and channels <= 2)
    if choice.delivery == "remux" and not (video_copy and legacy_aac):
        return unsupported("Forced Remux requires compatible video and audio without quality reduction.")
    method = "remux" if video_copy and legacy_aac else "transcode"
    if method == "transcode" and policy.mode == "direct_only":
        return unsupported("Direct Play and Remux Only is enabled. This selection requires transcoding.")
    if method == "transcode" and (video.height or 0) >= 2160 and not policy.allow_4k:
        return unsupported(
            "4K transcoding is disabled by the Owner. Direct Play compatible 4K media or change settings."
        )
    return Decision(
        method=method,
        reason="Local container/audio-track remapping."
        if method == "remux"
        else "Local conversion is required by the selected video, audio, subtitle, or quality settings.",
        video_copy=video_copy,
        audio_copy=legacy_aac,
        audio_transcode=video_copy and not legacy_aac,
        output_height=height,
        bitrate_kbps=bitrate,
        burn_subtitle=burn,
        mime="application/vnd.apple.mpegurl",
        direct_play_blockers=blockers,
    )
