"""Local adapter hints and FFmpeg device arguments; inventory never proves support."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ENCODERS = {"software": "libx264", "qsv": "h264_qsv", "nvenc": "h264_nvenc", "amf": "h264_amf"}


def binary_identity(executable: str) -> tuple[str, int, int] | None:
    value = shutil.which(executable)
    if value is None:
        return None
    try:
        path = Path(value).resolve(strict=True)
        stat = path.stat()
        return str(path), stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def detected_gpus() -> list[str]:
    if os.name == "nt":
        # A read-only local WMI query; no media names, network lookup or shell input.
        powershell = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
            result = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "@(Get-CimInstance -ClassName Win32_VideoController | "
                    "Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            value = json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
            values = value if isinstance(value, list) else [value]
            return [item[:120] for item in values[:16] if isinstance(item, str) and item.isprintable()]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return []
    results = []
    for entry in Path("/sys/class/drm").glob("card[0-9]*/device/vendor"):
        try:
            vendor = entry.read_text(encoding="ascii").strip()
            results.append({"0x8086": "Intel", "0x10de": "NVIDIA", "0x1002": "AMD"}.get(vendor, "GPU"))
        except OSError:
            continue
    return results[:16]


def gpu_hint(encoder: str, inventory: list[str], device: str) -> str | None:
    vendor = {"qsv": "intel", "nvenc": "nvidia", "amf": "amd"}[encoder]
    matches = [gpu for gpu in inventory if vendor in gpu.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # WMI ordering is not the DXGI/NVENC device-index ordering. Do not claim
        # that an indexed test used the first similarly branded GPU in inventory.
        selection = "automatic device selection" if device == "auto" else f"encoder device {device}"
        return f"Multiple {vendor.upper()} adapters detected; {selection}"
    return None


def device_input_options(encoder: str, device: str) -> list[str]:
    if device == "auto":
        return []
    if encoder == "h264_qsv":
        child = device if os.name == "nt" else f"/dev/dri/renderD{128 + int(device)}"
        suffix = ",child_device_type=d3d11va" if os.name == "nt" else ""
        return ["-init_hw_device", f"qsv=bluereel:hw,child_device={child}{suffix}", "-filter_hw_device", "bluereel"]
    if encoder == "h264_amf":
        return ["-init_hw_device", f"d3d11va=bluereel:{device}", "-filter_hw_device", "bluereel"]
    return []


def device_output_options(encoder: str, device: str) -> list[str]:
    return ["-gpu", device] if encoder == "h264_nvenc" and device != "auto" else []


def video_filter(height: int, encoder: str, device: str) -> str:
    value = f"scale=-2:{height}"
    if encoder == "h264_amf" and device != "auto":
        value += ",format=nv12,hwupload"
    return value


def output_pixel_format(encoder: str, device: str) -> str:
    return "d3d11" if encoder == "h264_amf" and device != "auto" else "yuv420p"
