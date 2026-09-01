from __future__ import annotations

import os
import time
from pathlib import Path

from app.services.temp_cleanup import cleanup_stale_temp


def test_temp_cleanup_only_removes_stale_app_owned_entries(context, tmp_path: Path) -> None:
    root = context.config.temp_dir
    root.mkdir(parents=True, exist_ok=True)
    stale = root / "job-12345678"
    stale.mkdir()
    (stale / "work.bin").write_bytes(b"temporary")
    unrelated = root / "keep-this"
    unrelated.write_bytes(b"user file")
    recent = root / "probe-abcdefgh"
    recent.write_bytes(b"recent")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    assert cleanup_stale_temp(context.config, older_than_hours=24) == 1
    assert not stale.exists()
    assert unrelated.exists()
    assert recent.exists()
