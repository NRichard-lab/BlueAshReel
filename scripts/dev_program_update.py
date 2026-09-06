"""Transactional source-only development updates; never installs runtime components or restores data."""
from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path


def program_file(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
        raise ValueError("Only explicitly selected Python program sources can be updated")
    base = root.resolve(strict=True)
    target = base / relative
    if not target.resolve(strict=True).is_relative_to(base):
        raise ValueError("Program source escapes its component directory")
    for part in (target, *target.parents):
        if part == base.parent:
            break
        if part.is_symlink() or getattr(part.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked program sources are not supported")
    if not target.is_file():
        raise ValueError("Missing program source")
    return target


def replace_file(source: Path, target: Path) -> None:
    stage = target.with_name(target.name + ".development-update.tmp")
    if stage.exists():
        raise ValueError("Another program replacement is pending")
    try:
        with stage.open("xb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


def update_program(
    program: Path, staged: Path, backup: Path, names: Sequence[str], *,
    stop: Callable[[], None], start: Callable[[], None], validate: Callable[[], bool],
) -> dict[str, str]:
    """Validation must cover startup, identity/reconnection and real playback.

    A failed start or validation restores every selected original file before
    restarting. The immutable backup is retained, including on failed rollback.
    No function in this module reads, replaces or restores mutable Agent data.
    """
    if not names or len(set(names)) != len(names):
        raise ValueError("An explicit unique file list is required")
    originals = {name: program_file(program, name) for name in names}
    sources = {name: program_file(staged, name) for name in names}
    # Compile first without generating bytecode in either program directory.
    for source in sources.values():
        compile(source.read_bytes(), "<reviewed Agent source>", "exec")
    backup.mkdir(parents=True, exist_ok=False)
    for name, source in originals.items():
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    stop()
    try:
        for name in names:
            replace_file(sources[name], originals[name])
        start()
        if not validate():
            raise RuntimeError("Updated Agent failed startup, reconnection or playback validation")
    except BaseException:  # noqa: BLE001 - restore program files even when validation is interrupted
        stop()
        for name in names:
            replace_file(backup / name, originals[name])
        start()
        raise RuntimeError("Updated program files were rolled back automatically") from None
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in originals.items()}
