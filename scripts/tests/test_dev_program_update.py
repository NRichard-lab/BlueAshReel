from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("dev_program_update", Path(__file__).parents[1] / "dev_program_update.py")
assert spec and spec.loader
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


@pytest.mark.parametrize("failure", ["startup", "reconnect", "playback"])
def test_failed_validation_restores_all_program_files_and_restarts(tmp_path, failure):
    program, staged = tmp_path / "program", tmp_path / "staged"
    program.mkdir(); staged.mkdir()
    for name in ["a.py", "b.py"]:
        (program / name).write_text("original = True\n")
        (staged / name).write_text("updated = True\n")
    events = []

    def start():
        events.append("start")
        if failure == "startup" and events.count("start") == 1:
            raise RuntimeError("Synthetic startup failure")

    with pytest.raises(RuntimeError, match="rolled back automatically"):
        update.update_program(program, staged, tmp_path / "backup", ["a.py", "b.py"],
                              stop=lambda: events.append("stop"), start=start, validate=lambda: False)
    assert events == ["stop", "start", "stop", "start"]
    assert all((program / name).read_text() == "original = True\n" for name in ["a.py", "b.py"])


def test_success_keeps_updated_files_and_rejects_escape_or_runtime_components(tmp_path):
    program, staged = tmp_path / "program", tmp_path / "staged"
    program.mkdir(); staged.mkdir()
    (program / "a.py").write_text("original = True\n")
    (staged / "a.py").write_text("updated = True\n")
    result = update.update_program(program, staged, tmp_path / "backup", ["a.py"],
                                   stop=lambda: None, start=lambda: None, validate=lambda: True)
    assert len(result["a.py"]) == 64 and (program / "a.py").read_text() == "updated = True\n"
    for name in ["../outside.py", "runtime.dll", "missing.py"]:
        with pytest.raises((ValueError, FileNotFoundError)):
            update.program_file(program, name)
