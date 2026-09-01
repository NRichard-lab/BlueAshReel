from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from app.config import AppConfig

_OWNED_TEMP_NAME = re.compile(r"^(?:job|probe|transcode)-[A-Za-z0-9_-]{8,}$")


def cleanup_stale_temp(config: AppConfig, *, older_than_hours: int = 24) -> int:
    """Remove only stale, explicitly app-named entries without following links."""
    root = config.temp_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - older_than_hours * 3600
    removed = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for entry in entries:
        if not _OWNED_TEMP_NAME.fullmatch(entry.name) or entry.is_symlink():
            continue
        candidate = Path(entry.path)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(resolved)
            elif entry.is_file(follow_symlinks=False):
                resolved.unlink()
            else:
                continue
            removed += 1
        except (OSError, ValueError):
            continue
    return removed
