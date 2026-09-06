"""Malformed local consent cannot become an approval or leave stale IPC files."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app import native_consent
from app.config import AppConfig


@pytest.mark.parametrize("payload", [[], True, None, "approved"])
def test_nonobject_confirmation_is_rejected_and_cleaned(tmp_path: Path, payload: object) -> None:
    async def scenario() -> None:
        with patch.object(native_consent, "consent_directory", return_value=tmp_path):
            task = asyncio.create_task(native_consent._request(
                AppConfig.model_construct(), "confirmation", {"message": "Compare fingerprint"}, timeout=2))
            await asyncio.sleep(0)
            request_path = next(tmp_path.glob("request-*.json"))
            request = json.loads(request_path.read_text())
            response_path = tmp_path / ("response-" + request["id"] + ".json")
            response_path.write_text(json.dumps(payload), encoding="utf-8")
            with pytest.raises(ValueError, match="Invalid local confirmation response"):
                await task
            assert not list(tmp_path.glob("*.json"))

    asyncio.run(scenario())
