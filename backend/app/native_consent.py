"""Bounded, private IPC with the logged-in user's native tray.

Only authenticated Owner operations call this module. A request is never consent:
the native dialog must produce a matching, unexpired affirmative response.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.native_install import write_json
from app.services.paths import assert_no_link_components, validate_windows_path_text


def consent_directory(config: AppConfig) -> Path:
    if config.deployment_mode != "native_windows" or config.native_data_dir is None:
        raise ValueError("Folder selection requires the Windows Agent workstation")
    result = config.native_data_dir / "state/tray-requests"
    assert_no_link_components(result.parent)
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert_no_link_components(result)
    return result


def validate_selected_folder(config: AppConfig, value: str) -> Path:
    if os.name == "nt":
        validate_windows_path_text(value)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ValueError("Select a dedicated, absolute media folder")
    assert_no_link_components(path)
    path = path.resolve(strict=True)
    for protected in (config.native_program_dir, config.native_data_dir, config.app_data_dir,
                      config.artwork_dir, config.temp_dir):
        if protected and (path == protected or path.is_relative_to(protected) or protected.is_relative_to(path)):
            raise ValueError("Media folders cannot overlap Agent storage")
    if not path.is_dir():
        raise ValueError("The selected folder is unavailable")
    with os.scandir(path) as entries:
        next(entries, None)  # Verify directory read permission without changing source media.
    return path


async def _request(config: AppConfig, kind: str, fields: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
    directory = consent_directory(config)
    if len(list(directory.glob("request-*.json"))) >= 4:
        raise ValueError("Another local confirmation is waiting on the Agent workstation")
    request_id = uuid.uuid4().hex
    request = directory / f"request-{request_id}.json"
    response = directory / f"response-{request_id}.json"
    expires = time.time() + timeout
    write_json(request, {"id": request_id, "kind": kind, "expires_at": expires, **fields})
    try:
        while time.time() < expires:
            if response.exists():
                assert_no_link_components(response)
                if response.stat().st_size > 16384:
                    raise ValueError("Invalid local confirmation response")
                value = json.loads(response.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("Invalid local confirmation response")
                if value.get("id") != request_id or value.get("approved") is not True:
                    return {}
                return value
            await asyncio.sleep(0.25)
        return {}
    finally:
        request.unlink(missing_ok=True)
        response.unlink(missing_ok=True)


async def request_folder(config: AppConfig, manual_path: str | None = None) -> Path | None:
    if manual_path is not None:
        validate_selected_folder(config, manual_path)
    result = await _request(config, "folder", {"path": manual_path})
    return validate_selected_folder(config, result["path"]) if result.get("path") else None


async def request_confirmation(config: AppConfig, message: str) -> bool:
    if not isinstance(message, str) or len(message) > 2000:
        raise ValueError("Invalid local confirmation")
    return bool(await _request(config, "confirmation", {"message": message}))
