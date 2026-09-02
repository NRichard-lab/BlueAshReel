from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("\\'", "'")
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def test_compose_keeps_primary_media_compatibility_and_runtime_definitions() -> None:
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    environment = compose["x-common-environment"]
    assert environment["MEDIA_ROOTS"] == "${MEDIA_ROOTS:-/media}"
    assert environment["MEDIA_ROOT_DEFINITIONS"] == "${MEDIA_ROOT_DEFINITIONS:-}"

    for service_name in ("backend", "worker"):
        mounts = compose["services"][service_name]["volumes"]
        media_mount = next(item for item in mounts if item["target"] == "/media")
        assert media_mount == {
            "type": "bind",
            "source": "${MEDIA_PATH:?Run the bootstrap script or set an existing readable MEDIA_PATH in .env}",
            "target": "/media",
            "read_only": True,
        }
    for service_name in ("frontend", "proxy"):
        assert all(
            not str(item.get("target", "")).startswith("/media")
            for item in compose["services"][service_name].get("volumes", [])
            if isinstance(item, dict)
        )


def test_safe_defaults_and_generated_local_files_are_excluded() -> None:
    example = dotenv(ROOT / ".env.example")
    assert example["MEDIA_PATH"] == "./media"
    assert example["MEDIA_ROOTS"] == "/media"
    assert example["MEDIA_ROOT_DEFINITIONS"] == '[{"id":"primary","display_name":"Media","path":"/media"}]'
    assert not re.search(r"[A-Za-z]:[/\\]", (ROOT / ".env.example").read_text(encoding="utf-8"))

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {
        "/.bluereel/",
        "/compose.override.yml",
        "/compose.override.yaml",
        "/.compose.override.yml.tmp.*",
        "/compose.media-roots.tmp.*.yml",
        "/..env.tmp.*",
    } <= set(gitignore)
    assert {
        ".bluereel",
        "compose.override.yml",
        "compose.override.yaml",
        ".compose.override.yml.tmp.*",
        "compose.media-roots.tmp.*.yml",
        "..env.tmp.*",
        ".env.*",
    } <= set(dockerignore)


@pytest.mark.skipif(shutil.which("git") is None, reason="Git unavailable")
def test_interrupted_bootstrap_candidates_are_ignored(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(ROOT / ".gitignore", checkout / ".gitignore")
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True, capture_output=True)
    candidates = [
        ".compose.override.yml.tmp.1234",
        ".compose.override.yml.tmp.0123456789abcdef0123456789abcdef",
        "compose.media-roots.tmp.0123456789abcdef0123456789abcdef.yml",
        "..env.tmp.0123456789abcdef0123456789abcdef",
        ".env.tmp.1234",
        ".env.media-roots.tmp.1234",
        ".env.media-roots.tmp.1234.tmp.1234",
        ".env.media-roots.tmp.0123456789abcdef0123456789abcdef",
        ".bluereel/.media-roots.tsv.tmp.1234",
        ".bluereel/.media-roots.tsv.tmp.0123456789abcdef0123456789abcdef",
        ".bluereel/.media-root-mounts.tmp.1234",
    ]
    result = subprocess.run(
        ["git", "-C", str(checkout), "check-ignore", "--no-index", "--stdin", "-z"],
        input=("\0".join(candidates) + "\0").encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().rstrip("\0").split("\0") == candidates


def test_bootstrap_contracts_are_explicit_and_fail_closed() -> None:
    powershell = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    for content in (powershell, shell):
        assert "MEDIA_ROOT_DEFINITIONS" in content
        assert "compose.override.yml" in content
        assert "media-roots.tsv" in content
        assert "/media-roots/" in content
        assert "controlled container recreation" in content
        assert "not managed by BlueReel" in content
    assert "FolderBrowserDialog" in powershell
    assert "-ConfigureMediaRoots" in powershell
    assert "--configure-media-roots" in shell
    assert "--skip-media-roots" in shell


def _make_fake_checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    shutil.copy2(ROOT / "scripts/bootstrap.sh", checkout / "scripts/bootstrap.sh")
    shutil.copy2(ROOT / "scripts/bootstrap.ps1", checkout / "scripts/bootstrap.ps1")
    shutil.copy2(ROOT / ".env.example", checkout / ".env.example")
    shutil.copy2(ROOT / "compose.yml", checkout / "compose.yml")
    shutil.copy2(ROOT / "config/product.json", checkout / "config/product.json")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    first = tmp_path / "First # O'Brien Media"
    second = tmp_path / "Second Media"
    first.mkdir()
    second.mkdir()
    return checkout, first, second


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None, reason="Windows PowerShell test")
def test_windows_bootstrap_preserves_secret_and_generates_stable_multi_root_registry(tmp_path: Path) -> None:
    checkout, first, second = _make_fake_checkout(tmp_path)
    docker_cmd = tmp_path / "bin/docker.cmd"
    docker_cmd.write_text("@exit /b 0\r\n", encoding="ascii")
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"
    quoted_script = str(checkout / "scripts/bootstrap.ps1").replace("'", "''")
    quoted_first = str(first).replace("'", "''")
    quoted_second = str(second).replace("'", "''")
    call = f"& '{quoted_script}' -MediaPath @('{quoted_first}','{quoted_second}') -NoStart"
    invocation = f"try {{ {call} }} catch {{ Write-Error $_.ScriptStackTrace; throw }}"
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", invocation]
    first_run = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
    assert first_run.returncode == 0, first_run.stderr + first_run.stdout
    values = dotenv(checkout / ".env")
    secret = values["APP_SECRET_KEY"]
    assert len(secret) >= 64
    assert values["MEDIA_PATH"] == first.resolve().as_posix()
    assert values["MEDIA_ROOTS"].startswith("/media:/media-roots/root_")
    assert str(first) not in values["MEDIA_ROOT_DEFINITIONS"]
    raw_environment = (checkout / ".env").read_text(encoding="utf-8")
    escaped_env_path = first.resolve().as_posix().replace("'", "\\'")
    assert "MEDIA_PATH='" + escaped_env_path + "'" in raw_environment
    assert "MEDIA_ROOT_DEFINITIONS='[" in raw_environment
    registry = (checkout / ".bluereel/media-roots.tsv").read_text(encoding="utf-8")
    assert f"primary\t{first.name}\t/media\t{first.resolve().as_posix()}" in registry
    records = [line.split("\t") for line in registry.splitlines()[1:]]
    assert re.fullmatch(r"root_[0-9a-f]{16}", records[1][0])
    override = yaml.safe_load((checkout / "compose.override.yml").read_text(encoding="utf-8"))
    assert set(override["services"]) == {"backend", "worker"}
    assert override["services"]["backend"]["volumes"] == override["services"]["worker"]["volumes"]
    assert all(item["read_only"] is True for item in override["services"]["backend"]["volumes"])
    actual_docker = shutil.which("docker", path=os.environ.get("PATH"))
    if actual_docker:
        rendered = subprocess.run(
            [
                actual_docker,
                "compose",
                "--env-file",
                str(checkout / ".env"),
                "-f",
                str(checkout / "compose.yml"),
                "-f",
                str(checkout / "compose.override.yml"),
                "config",
                "--format",
                "json",
            ],
            cwd=checkout,
            text=True,
            capture_output=True,
            check=False,
        )
        assert rendered.returncode == 0, rendered.stderr
        merged = json.loads(rendered.stdout)
        backend_mounts = [item for item in merged["services"]["backend"]["volumes"] if item["target"].startswith("/media")]
        worker_mounts = [item for item in merged["services"]["worker"]["volumes"] if item["target"].startswith("/media")]
        assert backend_mounts == worker_mounts
        assert len(backend_mounts) == 2 and all(item["read_only"] is True for item in backend_mounts)
        rendered_sources = {item["source"].replace("\\", "/") for item in backend_mounts}
        assert rendered_sources == {first.resolve().as_posix(), second.resolve().as_posix()}
        assert all(
            not item["target"].startswith("/media")
            for service_name in ("frontend", "proxy")
            for item in merged["services"][service_name].get("volumes", [])
        )
        definitions = merged["services"]["backend"]["environment"]["MEDIA_ROOT_DEFINITIONS"]
        parsed_definitions = json.loads(definitions)
        assert len(parsed_definitions) == 2
        assert parsed_definitions[0]["display_name"] == first.name

    unchanged_environment = (checkout / ".env").read_bytes()
    reversed_call = (
        f"& '{quoted_script}' -ConfigureMediaRoots -MediaPath "
        f"@('{quoted_second}','{quoted_first}') -NoStart"
    )
    reversed_invocation = f"try {{ {reversed_call} }} catch {{ Write-Error $_.ScriptStackTrace; throw }}"
    reordered = subprocess.run(
        command[:-1] + [reversed_invocation],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reordered.returncode != 0
    assert "primary media root must remain first" in reordered.stderr + reordered.stdout
    assert (checkout / ".env").read_bytes() == unchanged_environment
    assert (checkout / ".bluereel/media-roots.tsv").read_text(encoding="utf-8") == registry

    silent_change = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
    assert silent_change.returncode != 0
    assert "-ConfigureMediaRoots" in silent_change.stderr + silent_change.stdout
    assert (checkout / ".env").read_bytes() == unchanged_environment

    reinvocation = invocation.replace(" -NoStart", " -ConfigureMediaRoots -NoStart")
    second_run = subprocess.run(
        command[:-1] + [reinvocation],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second_run.returncode == 0, second_run.stderr + second_run.stdout
    assert dotenv(checkout / ".env")["APP_SECRET_KEY"] == secret
    assert (checkout / ".bluereel/media-roots.tsv").read_text(encoding="utf-8") == registry

    dollar_root = tmp_path / "Dollar $ Media"
    dollar_root.mkdir()
    quoted_dollar = str(dollar_root).replace("'", "''")
    dollar_call = f"& '{quoted_script}' -ConfigureMediaRoots -MediaPath '{quoted_dollar}' -NoStart"
    dollar_invocation = f"try {{ {dollar_call} }} catch {{ Write-Error $_.ScriptStackTrace; throw }}"
    before_dollar = (checkout / ".env").read_bytes()
    rejected_dollar = subprocess.run(
        command[:-1] + [dollar_invocation],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected_dollar.returncode != 0
    assert "dollar sign" in rejected_dollar.stderr + rejected_dollar.stdout
    assert (checkout / ".env").read_bytes() == before_dollar

    (checkout / "compose.override.yml").write_text("services: {}\n", encoding="utf-8")
    before_foreign_override = (checkout / ".env").read_bytes()
    refused = subprocess.run(
        command[:-1] + [reinvocation],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode != 0
    assert "not managed by BlueReel" in refused.stderr + refused.stdout
    assert (checkout / ".env").read_bytes() == before_foreign_override


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None, reason="Windows PowerShell test")
def test_windows_bootstrap_explicit_skip_keeps_safe_default(tmp_path: Path) -> None:
    checkout, _first, _second = _make_fake_checkout(tmp_path)
    docker_cmd = tmp_path / "bin/docker.cmd"
    docker_cmd.write_text("@exit /b 0\r\n", encoding="ascii")
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(checkout / "scripts/bootstrap.ps1"),
            "-SkipMediaRoots",
            "-NoStart",
        ],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert dotenv(checkout / ".env")["MEDIA_PATH"] == "./media"
    assert (checkout / "media").is_dir()
    assert not (checkout / ".bluereel").exists()
    assert not (checkout / "compose.override.yml").exists()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0 or shutil.which("sh") is None, reason="POSIX non-root test")
def test_linux_bootstrap_generates_stable_read_only_multi_root_configuration(tmp_path: Path) -> None:
    checkout, first, second = _make_fake_checkout(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"
    command = [
        "sh",
        str(checkout / "scripts/bootstrap.sh"),
        "--configure-media-roots",
        "--media-path",
        str(first),
        "--media-path",
        str(second),
        "--no-start",
    ]
    first_run = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
    assert first_run.returncode == 0, first_run.stderr

    values = dotenv(checkout / ".env")
    secret = values["APP_SECRET_KEY"]
    assert len(secret) >= 64
    assert values["MEDIA_PATH"] == str(first.resolve())
    assert values["MEDIA_ROOTS"].startswith("/media:/media-roots/root_")
    assert str(first) not in values["MEDIA_ROOT_DEFINITIONS"]
    assert str(second) not in values["MEDIA_ROOT_DEFINITIONS"]
    raw_environment = (checkout / ".env").read_text(encoding="utf-8")
    escaped_env_path = first.resolve().as_posix().replace("'", "\\'")
    assert "MEDIA_PATH='" + escaped_env_path + "'" in raw_environment
    assert "MEDIA_ROOT_DEFINITIONS='[" in raw_environment

    registry = (checkout / ".bluereel/media-roots.tsv").read_text(encoding="utf-8")
    assert "\tprimary\t" not in registry
    records = [line.split("\t") for line in registry.splitlines()[1:]]
    assert records[0][0:3] == ["primary", first.name, "/media"]
    assert re.fullmatch(r"root_[0-9a-f]{16}", records[1][0])
    override = yaml.safe_load((checkout / "compose.override.yml").read_text(encoding="utf-8"))
    assert set(override["services"]) == {"backend", "worker"}
    assert override["services"]["backend"]["volumes"] == override["services"]["worker"]["volumes"]
    assert all(item["read_only"] is True for item in override["services"]["backend"]["volumes"])

    second_run = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
    assert second_run.returncode == 0, second_run.stderr
    assert dotenv(checkout / ".env")["APP_SECRET_KEY"] == secret
    assert (checkout / ".bluereel/media-roots.tsv").read_text(encoding="utf-8") == registry


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0 or shutil.which("sh") is None, reason="POSIX non-root test")
def test_linux_bootstrap_refuses_silent_existing_configuration_change(tmp_path: Path) -> None:
    checkout, first, _second = _make_fake_checkout(tmp_path)
    shutil.copy2(checkout / ".env.example", checkout / ".env")
    before = (checkout / ".env").read_bytes()
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"
    result = subprocess.run(
        ["sh", str(checkout / "scripts/bootstrap.sh"), "--media-path", str(first), "--no-start"],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--configure-media-roots" in result.stderr
    assert (checkout / ".env").read_bytes() == before
