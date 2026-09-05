#!/usr/bin/env python3
"""Opt-in HTTP acceptance checks for the disposable native Development instance.

Never import the backend, open its database, change Windows services, or use port
8080. Credentials come from BLUEREEL_ACCEPTANCE_USERNAME/PASSWORD or an existing
private JSON file. Evidence contains assertions and selected public diagnostics,
not authentication responses, cookies, passwords, or raw server error bodies.
Generated fixtures and test libraries are retained for visual QA. Only the new
test Viewer is disabled, owned sessions stopped, and the original policy restored.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
MEDIA_ROOT = Path(r"C:\ProgramData\BlueAshReel-Development-TestMedia")
PROGRAM_ROOT = Path(r"C:\Program Files\BlueAshReel Development")
CAPS = {"h264": True, "aac": True, "hls": True, "max_height": 2160, "max_h264_level": 51}
MODES = ("automatic", "hardware_preferred", "software_only", "direct_only", "hardware_required")


class AcceptanceError(RuntimeError):
    """A safe, credential-free assertion description."""


def check(condition: Any, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def ordinary_path(path: Path) -> Path:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        check(not candidate.is_symlink(), "Acceptance paths cannot contain symlinks")
        check(not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
              "Acceptance paths cannot contain junctions or reparse points")
    return path.resolve()


def target_url(value: str, allowed: bool) -> str:
    check(allowed, "Explicit --allow-disposable-instance is required")
    parsed = urllib.parse.urlsplit(value)
    check(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"},
          "Acceptance target must be HTTP loopback")
    check(parsed.port == 18080, "Only the disposable native port 18080 is permitted; production 8080 is forbidden")
    check(not parsed.username and not parsed.password and parsed.path in {"", "/"}
          and not parsed.query and not parsed.fragment, "Acceptance target must contain only its loopback origin")
    return value.rstrip("/")


def credentials(path: Path | None) -> tuple[str, str]:
    if path:
        ordinary_path(path)
        check(path.is_file() and path.stat().st_size <= 4096, "Private credential file is missing or invalid")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        username, password = data.get("username"), data.get("password")
    else:
        username = os.getenv("BLUEREEL_ACCEPTANCE_USERNAME")
        password = os.getenv("BLUEREEL_ACCEPTANCE_PASSWORD")
    check(isinstance(username, str) and isinstance(password, str) and username and len(password) >= 12,
          "Set acceptance credential environment variables or supply a private JSON credential file")
    return str(username), str(password)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class Api:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect(),
        )
        self.csrf = ""
        self.creates: list[float] = []

    def raw(self, method: str, path: str, data: Any = None, *, expected: tuple[int, ...] = (200,),
            headers: dict[str, str] | None = None, timeout: int = 120) -> tuple[int, dict[str, str], bytes]:
        check(path.startswith("/api/v1/") and "\\" not in path and "\r" not in path and "\n" not in path,
              "API requests must stay in the local application API")
        if method == "POST" and path == "/api/v1/playback/sessions":
            now = time.monotonic()
            self.creates = [when for when in self.creates if when > now - 61]
            if len(self.creates) >= 10:
                deadline = self.creates[0] + 61
                print("Respecting the service playback admission rate limit.", flush=True)
                while time.monotonic() < deadline:
                    time.sleep(min(10, deadline - time.monotonic()))
            self.creates.append(time.monotonic())
        request_headers = {"Accept": "application/json", "Origin": self.origin}
        request_headers.update(headers or {})
        if method not in {"GET", "HEAD"} and self.csrf:
            request_headers["X-CSRF-Token"] = self.csrf
        body = None if data is None else json.dumps(data).encode("utf-8")
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, body, request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status_code = response.code
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            content = response.read(32 * 1024 * 1024 + 1)
        check(len(content) <= 32 * 1024 * 1024, "API response exceeded acceptance safety limit")
        check(status_code in expected, f"HTTP {method} {path.split('?')[0]} returned {status_code}; expected {expected}")
        return status_code, response_headers, content

    def json(self, method: str, path: str, data: Any = None, *, expected: tuple[int, ...] = (200,)) -> Any:
        _, _, content = self.raw(method, path, data, expected=expected)
        return json.loads(content) if content else None

    def login(self, username: str, password: str, *, create_owner: bool = False) -> None:
        if create_owner:
            check(self.json("GET", "/api/v1/setup/status")["setup_required"], "Owner already exists; omit --create-owner")
            self.csrf = self.json("POST", "/api/v1/setup/session", {})["csrf_token"]
            check(self.json("GET", "/api/v1/media-roots")["platform"] == "windows",
                  "Owner creation is permitted only on a verified native Windows instance")
        endpoint = "/api/v1/setup/owner" if create_owner else "/api/v1/auth/login"
        result = self.json("POST", endpoint, {"username": username, "password": password},
                           expected=(201,) if create_owner else (200,))
        self.csrf = result["csrf_token"]


def encode(executable: Path, arguments: list[str]) -> None:
    result = subprocess.run([str(executable), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", *arguments],
                            capture_output=True, timeout=120, check=False)
    check(result.returncode == 0, "Synthetic fixture encoding failed; raw encoder output was not logged")


def record_resume_checkpoint(api: Api, session_id: str) -> None:
    endpoint = f"/api/v1/playback/{session_id}/progress"
    # The shared server deliberately does not create watch history from a seek
    # alone: at least two seconds of elapsed, advancing playback are required.
    api.json("POST", endpoint, {"position_seconds": 0, "playing": True, "reason": "playing", "sequence": 1})
    time.sleep(2.25)
    api.json("POST", endpoint, {"position_seconds": 2.1, "playing": False, "reason": "pause", "sequence": 2})
    api.json("POST", endpoint, {"position_seconds": 8, "playing": False, "reason": "seek", "sequence": 3})


def fixture_tree(root: Path, run_id: str, ffmpeg: Path) -> tuple[Path, dict[str, Path]]:
    check(root == MEDIA_ROOT, "Only the dedicated Development-TestMedia fixture root is allowed")
    ordinary_path(root)
    check(root.is_dir(), "Approve and create the dedicated Development-TestMedia root before acceptance")
    check(re.fullmatch(r"[a-f0-9]{12}", run_id), "Invalid acceptance run identifier")
    run_root = root / ("Acceptance-" + run_id)
    run_root.mkdir()  # Never reuse or overwrite another run's source fixtures.
    for folder in ("Movies", "TV", "Private"):
        (run_root / folder).mkdir()
    paths = {name: run_root / "Movies" / filename for name, filename in {
        "direct": "Direct.Test.2026.mp4", "remux": "Container.Test.2026.mkv",
        "video": "Video.Conversion.2026.mkv", "audio": "Audio.Conversion.2026.mkv",
        "tracks": "Audio.Selection.2026.mkv", "loss": "Source.Loss.2026.mp4",
    }.items()}
    encode(ffmpeg, ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=12", "-f", "lavfi", "-i",
                   "sine=frequency=440:sample_rate=48000", "-t", "24", "-c:v", "libx264", "-preset", "ultrafast",
                   "-threads", "1", "-pix_fmt", "yuv420p", "-g", "24", "-c:a", "aac", "-ac", "2",
                   "-movflags", "+faststart", str(paths["direct"])])
    encode(ffmpeg, ["-i", str(paths["direct"]), "-map", "0", "-c", "copy", str(paths["remux"])])
    encode(ffmpeg, ["-i", str(paths["direct"]), "-c:v", "mpeg4", "-q:v", "7", "-threads", "1",
                   "-c:a", "aac", str(paths["video"])])
    encode(ffmpeg, ["-i", str(paths["direct"]), "-c:v", "copy", "-c:a", "ac3", str(paths["audio"])])
    captions = run_root / "generated-captions.srt"
    captions.write_text("1\n00:00:00,500 --> 00:00:03,500\nBlueReel synthetic acceptance subtitle.\n", encoding="utf-8")
    encode(ffmpeg, ["-i", str(paths["direct"]), "-i", str(captions), "-map", "0:v:0", "-map", "0:a:0",
                   "-map", "0:a:0", "-map", "1:0", "-c", "copy", "-c:s", "srt",
                   "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=spa", str(paths["tracks"])])
    paths["private"] = run_root / "Private" / "Private.Test.2026.mp4"
    paths["episode1"] = run_root / "TV" / "Local.Series.S01E01.Episode.1.mp4"
    paths["episode2"] = run_root / "TV" / "Local.Series.S01E02.Episode.2.mp4"
    for key in ("loss", "private", "episode1", "episode2"):
        shutil.copyfile(paths["direct"], paths[key])
    return run_root, paths


@contextlib.contextmanager
def temporarily_missing(source: Path, run_root: Path, expected_hash: str) -> Iterator[None]:
    canonical = ordinary_path(source)
    check(canonical.is_relative_to(ordinary_path(run_root)) and source.name == "Source.Loss.2026.mp4",
          "Source-loss mutation is restricted to the exact generated fixture")
    check(source.is_file() and source.stat().st_nlink == 1 and sha256(source) == expected_hash,
          "Source-loss fixture identity changed; refusing mutation")
    held = source.with_name(source.name + ".acceptance-held")
    check(not held.exists(), "A held source-loss fixture already exists; refusing overwrite")
    source.rename(held)
    try:
        yield
    finally:
        check(not source.exists(), "Source-loss destination unexpectedly reappeared; held original remains recoverable")
        held.rename(source)
        check(sha256(source) == expected_hash, "Source-loss fixture restoration checksum mismatch")


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.api = Api(target_url(args.base_url, args.allow_disposable_instance))
        self.run_id = uuid.uuid4().hex[:12]
        self.cases: list[dict[str, Any]] = []
        self.sessions: dict[str, Api] = {}
        self.original_policy: dict[str, Any] | None = None
        self.policy: dict[str, Any] = {}
        self.viewer_id: str | None = None
        self.run_root = MEDIA_ROOT / ("Acceptance-" + self.run_id)
        self.paths: dict[str, Path] = {}
        self.media: dict[str, dict[str, Any]] = {}
        self.libraries: dict[str, str] = {}
        self.output = ordinary_path(args.output_dir) / ("acceptance-api-" + self.run_id)
        check(any(self.output.is_relative_to(REPOSITORY / "artifacts" / directory)
                  for directory in ("native-dev", "development")),
              "Acceptance evidence must be inside an ignored native artifact directory")
        self.output.mkdir(parents=True)

    def case(self, name: str, operation: Callable[[], Any]) -> Any:
        started = time.monotonic()
        row: dict[str, Any] = {"name": name}
        try:
            value = operation()
            row.update({"status": "passed", "evidence": value})
            return value
        except AcceptanceError as error:
            row.update({"status": "failed", "reason": str(error)})
        except Exception as error:  # noqa: BLE001 - Evidence boundary must not expose secrets in raw exceptions.
            row.update({"status": "failed", "reason": "Unexpected " + type(error).__name__ + "; raw details withheld"})
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.cases.append(row)
            print(name + ": " + row["status"], flush=True)
            self.write_evidence()
        return None

    def write_evidence(self) -> None:
        document = {
            "schema_version": 1, "run_id": self.run_id, "target": self.api.origin,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(), "cases": self.cases,
            "source_fixture_directory": str(self.run_root),
            "notes": ["Opt-in disposable native instance only; no direct database access.",
                      "Generated fixtures/libraries retained. Test Viewer disabled at cleanup.",
                      "OS firewall, service identity, browser decoding, UAC and reboot are separate acceptance checks."],
        }
        (self.output / "evidence.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def preflight(self) -> dict[str, Any]:
        check(os.name == "nt", "The native acceptance harness requires Windows")
        username, password = credentials(self.args.credentials_file)
        self.api.login(username, password, create_owner=self.args.create_owner)
        check("Owner" in self.api.json("GET", "/api/v1/auth/me")["roles"], "Acceptance requires an Owner account")
        storage = self.api.json("GET", "/api/v1/media-storage")
        check(storage.get("platform") == "windows", "Target is not the native Windows deployment")
        approved = [Path(row["internal_path"]).resolve() for row in storage["items"] if row.get("internal_path")]
        check(MEDIA_ROOT.resolve() in approved, "The exact disposable test-media root must be approved")
        check(self.api.json("GET", "/api/v1/streams")["total"] == 0,
              "Stop existing playback in the disposable instance before acceptance")
        self.original_policy = self.api.json("GET", "/api/v1/transcoding-policy")
        self.policy = dict(self.original_policy)
        ffmpeg = ordinary_path(self.args.ffmpeg)
        check(ffmpeg.is_file() and ffmpeg.name.lower() == "ffmpeg.exe", "Bundled FFmpeg executable is unavailable")
        manifest_path = ffmpeg.parents[2] / "included-components.json"
        check(manifest_path.is_file(), "FFmpeg must belong to a verified native payload with included-components.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = next((item["sha256"] for item in manifest["files"] if item["path"] == "runtime/ffmpeg/ffmpeg.exe"), None)
        check(expected == sha256(ffmpeg), "Bundled FFmpeg does not match its payload manifest")
        return {"platform": "windows", "version": manifest["version"], "ffmpeg_sha256": expected,
                "initial_transcoding_mode": self.policy["mode"], "initial_streams": 0}

    def create_catalog(self) -> dict[str, Any]:
        self.run_root, self.paths = fixture_tree(MEDIA_ROOT, self.run_id, self.args.ffmpeg)
        for name, kind in (("Movies", "movies"), ("TV", "tv"), ("Private", "movies")):
            library = self.api.json("POST", "/api/v1/libraries", {
                "name": "Acceptance " + self.run_id + " " + name, "library_type": kind,
                "paths": [str(self.run_root / name)],
            }, expected=(201,))
            self.libraries[name] = library["id"]
            job = self.api.json("POST", f"/api/v1/libraries/{library['id']}/scans", {"mode": "full"}, expected=(202,))
            deadline = time.monotonic() + self.args.scan_timeout
            while True:
                state = self.api.json("GET", f"/api/v1/jobs/{job['job_id']}")
                if state["status"] not in {"queued", "running", "retry_wait"}:
                    break
                check(time.monotonic() < deadline, "Worker scan timed out")
                time.sleep(0.5)
            check(state["status"] == "succeeded", "Worker did not complete the generated-media scan")
            query = urllib.parse.urlencode({"library_id": library["id"], "page_size": 100})
            rows = self.api.json("GET", "/api/v1/browse/media?" + query)["items"]
            for row in rows:
                if not row.get("file_id"):
                    continue
                detail = self.api.json("GET", f"/api/v1/browse/media/{row['id']}")
                key = self.identify(detail)
                if key:
                    self.media[key] = detail
        check(set(self.paths) <= set(self.media), "Catalog did not identify every generated fixture")
        return {"libraries_created": len(self.libraries), "fixtures_analyzed": len(self.media),
                "worker_completed": True}

    @staticmethod
    def identify(item: dict[str, Any]) -> str | None:
        title = item["title"].lower()
        for prefix, key in (("direct test", "direct"), ("container test", "remux"), ("video conversion", "video"),
                            ("audio conversion", "audio"), ("audio selection", "tracks"), ("source loss", "loss"),
                            ("private test", "private")):
            if title.startswith(prefix):
                return key
        if item.get("kind") == "episode":
            number = item.get("episode_number")
            if number in {1, 2}:
                return "episode" + str(number)
        return None

    def set_policy(self, **changes: Any) -> None:
        self.policy = self.api.json("PATCH", "/api/v1/transcoding-policy", {**self.policy, **changes})
        check(all(self.policy[key] == value for key, value in changes.items()), "Transcoding policy did not persist")

    @contextlib.contextmanager
    def session(self, key: str, *, api: Api | None = None, **changes: Any) -> Iterator[dict[str, Any]]:
        client = api or self.api
        result = client.json("POST", "/api/v1/playback/sessions", {
            "file_id": self.media[key]["file_id"], "capabilities": CAPS, "position_seconds": 0, **changes,
        }, expected=(201,))
        self.sessions[result["id"]] = client
        try:
            yield result
        finally:
            client.raw("POST", f"/api/v1/playback/{result['id']}/stop", expected=(204,))
            self.sessions.pop(result["id"], None)

    def active(self, session: dict[str, Any]) -> dict[str, Any]:
        rows = self.api.json("GET", "/api/v1/streams")["items"]
        found = next((row for row in rows if row["id"] == session["id"]), None)
        check(found is not None and found["state"] == "active", "Owned playback is not truthfully listed active")
        assert found is not None
        return dict(found)

    def hls(self, session: dict[str, Any]) -> dict[str, Any]:
        _, _, contents = self.api.raw("GET", session["url"])
        text = contents.decode("utf-8")
        check(text.startswith("#EXTM3U") and "http://" not in text and "https://" not in text,
              "HLS manifest is not local-only")
        names = [line for line in text.splitlines() if line and not line.startswith("#")]
        check(names and all(re.fullmatch(r"segment-\d{6}\.ts", name) for name in names), "Unsafe HLS segment name")
        segment_url = session["url"].replace("index.m3u8", names[0])
        _, _, segment = self.api.raw("GET", segment_url)
        check(len(segment) > 188, "HLS segment is empty")
        segment_path = self.output / (session["id"] + ".ts")
        with segment_path.open("xb") as target:
            target.write(segment)
        probe = subprocess.run([str(self.args.ffmpeg.with_name("ffprobe.exe")), "-v", "error", "-show_streams",
                                "-of", "json", str(segment_path)], capture_output=True, timeout=30, check=False)
        check(probe.returncode == 0, "Downloaded HLS segment did not pass bundled FFprobe")
        streams = json.loads(probe.stdout)["streams"]
        video = [row["codec_name"] for row in streams if row["codec_type"] == "video"]
        audio = [row["codec_name"] for row in streams if row["codec_type"] == "audio"]
        check(video == ["h264"] and audio == ["aac"], "HLS codecs are not expected H.264/AAC")
        return {"segment_bytes": len(segment), "video": video, "audio": audio}

    def direct(self) -> dict[str, Any]:
        self.set_policy(mode="automatic", preferred_hardware="auto", hardware_device="auto")
        with self.session("direct") as session:
            check(session["decision"]["method"] == "direct", "Compatible MP4 did not Direct Play")
            _, head, body = self.api.raw("HEAD", session["url"])
            size = self.paths["direct"].stat().st_size
            check(not body and int(head["content-length"]) == size, "Direct HEAD size/body mismatch")
            for start in (0, 2048):
                _, headers, content = self.api.raw("GET", session["url"], expected=(206,),
                                                 headers={"Range": f"bytes={start}-{start + 255}"})
                with self.paths["direct"].open("rb") as source:
                    source.seek(start)
                    check(content == source.read(256), "Direct byte range differs from original fixture")
                check(headers["content-range"] == f"bytes {start}-{start + 255}/{size}", "Seek range metadata mismatch")
            self.api.raw("GET", session["url"], expected=(416,), headers={"Range": "bytes=1-2,4-5"})
            row = self.active(session)
            check(row["method_label"] == "Direct Play" and row["encoder"] == "none", "Direct Play mislabeled as encoding")
        self.api.raw("GET", session["url"], expected=(410,))
        return {"head": True, "original_ranges": 2, "multirange_rejected": True, "stopped_url": 410}

    def conversions(self) -> list[dict[str, Any]]:
        self.set_policy(mode="software_only", preferred_hardware="auto", cpu_preset="fast")
        results = []
        for key, method, encoder in (("remux", "remux", "copy"), ("video", "transcode", "libx264"),
                                     ("audio", "transcode", "copy")):
            with self.session(key) as session:
                check(session["decision"]["method"] == method, "Unexpected conversion decision")
                row = self.active(session)
                check(row["encoder"] == encoder, "Active Streams encoder differs from requested conversion")
                if key == "audio":
                    check(row["audio_encoder"] == "aac (CPU)", "Audio conversion not identified as CPU AAC")
                result = {"fixture": key, "method": method, "encoder": row["encoder"],
                          "audio_encoder": row["audio_encoder"], **self.hls(session)}
                if key == "video":
                    self.set_policy(mode="direct_only")
                    check(self.active(session)["selected_mode"] == "software_only", "Existing stream policy snapshot changed")
                    self.api.raw("GET", session["url"])
                    self.api.raw("POST", "/api/v1/playback/sessions", {
                        "file_id": self.media["video"]["file_id"], "capabilities": CAPS,
                    }, expected=(422,))
                    result["policy_switch_preserved_active"] = True
                    self.set_policy(mode="software_only")
            self.api.raw("GET", session["url"], expected=(410,))
            results.append(result)
        return results

    def modes(self) -> dict[str, Any]:
        self.set_policy(mode="automatic", preferred_hardware="auto", hardware_device="auto")
        self.api.json("POST", "/api/v1/playback-health/detect", {})
        health = self.api.json("GET", "/api/v1/playback-health")
        check(health["tested_ffmpeg_sha256"] == sha256(self.args.ffmpeg), "Hardware tests used a different FFmpeg binary")
        tests = health["hardware_tests"]
        check(all(row["last_test_at"] and row["available_codecs"] ==
                  (["h264"] if row["test_status"] == "passed" else []) for row in tests),
              "Hardware capability labels are not backed by timestamped H.264 tests")
        passed = sorted((row["encoder"] for row in tests if row["test_status"] == "passed"), key=lambda key: key != "amf")
        unavailable = next((row["encoder"] for row in tests if row["test_status"] == "failed"), None)
        selected = passed[0] if passed else (unavailable or "amf")
        with self.session("video") as session:
            automatic = self.active(session)
            check(automatic["selected_mode"] == "automatic", "Automatic conversion lost its policy snapshot")
            check(automatic["encoder"] in {"libx264", "h264_qsv", "h264_nvenc", "h264_amf"},
                  "Automatic conversion reported an unknown encoder")
            if automatic["fallback"]:
                check(automatic["encoder"] == "libx264" and automatic["fallback_reason"],
                      "Automatic fallback is not truthfully identified as software")
            automatic_result = {"encoder": automatic["encoder"], "fallback": automatic["fallback"], **self.hls(session)}
        direct_modes = []
        for mode in MODES:
            self.set_policy(mode=mode, preferred_hardware=selected)
            with self.session("direct") as session:
                check(session["decision"]["method"] == "direct", "A policy mode unnecessarily transcoded compatible MP4")
                check(self.active(session)["selected_mode"] == mode, "Active Streams mode snapshot mismatch")
            direct_modes.append(mode)
        hardware_result: dict[str, Any] = {"verified_encoders": passed}
        if passed:
            self.set_policy(mode="hardware_required", preferred_hardware=selected)
            with self.session("video") as session:
                row = self.active(session)
                check(row["encoder"] == "h264_" + selected and not row["fallback"],
                      "Hardware Required did not use the verified encoder")
                hardware_result.update({"actual_encoder": row["encoder"], **self.hls(session)})
        else:
            hardware_result["hardware_success"] = "not available to installed service identity"
        if unavailable:
            self.set_policy(mode="hardware_preferred", preferred_hardware=unavailable)
            with self.session("video") as session:
                row = self.active(session)
                check(row["encoder"] == "libx264" and row["fallback"] and row["fallback_reason"],
                      "Unavailable preferred hardware did not truthfully fall back to CPU")
                hardware_result["fallback"] = {"unavailable_encoder": unavailable, "actual_encoder": row["encoder"],
                                                "method_label": row["method_label"], **self.hls(session)}
            self.set_policy(mode="hardware_required", preferred_hardware=unavailable)
            self.api.raw("POST", "/api/v1/playback/sessions", {
                "file_id": self.media["video"]["file_id"], "capabilities": CAPS,
            }, expected=(422, 503))
            streams = self.api.json("GET", "/api/v1/streams")
            check(not streams["items"] and streams["failures"], "Failed Required conversion is not separated from active streams")
            hardware_result["required_unavailable_rejected_without_cpu"] = True
        else:
            hardware_result["fallback"] = "not exercised: every detected encoder passed"
        self.set_policy(mode="direct_only")
        with self.session("remux") as session:
            check(session["decision"]["method"] == "remux", "Direct/Remux Only rejected compatible remux")
            self.hls(session)
        self.api.raw("POST", "/api/v1/playback/sessions", {
            "file_id": self.media["remux"]["file_id"], "capabilities": CAPS, "position_seconds": 3,
        }, expected=(422,))
        self.api.raw("POST", "/api/v1/playback/sessions", {
            "file_id": self.media["audio"]["file_id"], "capabilities": CAPS,
        }, expected=(422,))
        return {"direct_modes": direct_modes, "automatic_conversion": automatic_result, "hardware": hardware_result,
                "tests": [{key: row[key] for key in ("encoder", "device", "test_status", "last_test_at", "available_codecs")}
                          for row in tests]}

    def tracks(self) -> dict[str, Any]:
        self.set_policy(mode="software_only")
        tracks = self.media["tracks"]["files"][0]
        check(len(tracks["audio"]) == 2 and tracks["subtitles"], "Audio/subtitle choices were not scanned")
        audio, subtitle = tracks["audio"][1]["index"], tracks["subtitles"][0]["index"]
        with self.session("tracks", audio_index=audio, subtitle_index=subtitle) as session:
            check(session["audio_index"] == audio and session["subtitle_index"] == subtitle, "Requested tracks changed")
            self.hls(session)
            _, headers, content = self.api.raw("GET", f"/api/v1/playback/{session['id']}/subtitles/{subtitle}.vtt")
            check(content.startswith(b"WEBVTT") and b"synthetic acceptance subtitle" in content,
                  "Local text subtitle extraction did not return expected WebVTT")
            check("text/vtt" in headers["content-type"], "Subtitle response MIME mismatch")
        with self.session("remux", position_seconds=3) as session:
            check(session["video_offset"] == 3 and session["decision"]["method"] == "transcode",
                  "Precise HLS seek did not preserve requested offset")
            self.hls(session)
        return {"audio_choices": 2, "selected_audio_index": audio, "text_subtitle_webvtt": True, "precise_seek_seconds": 3}

    def progress_and_viewer(self) -> dict[str, Any]:
        self.set_policy(mode="automatic", preferred_hardware="auto")
        password = secrets.token_urlsafe(30)
        username = "acceptance-viewer-" + self.run_id
        user = self.api.json("POST", "/api/v1/users", {
            "username": username, "password": password, "role": "Viewer",
            "library_ids": [self.libraries["Movies"], self.libraries["TV"]],
        }, expected=(201,))
        self.viewer_id = user["id"]
        viewer = Api(self.api.origin)
        viewer.login(username, password)
        for route in ("/streams", "/transcoding-policy", "/playback-health", "/media-storage"):
            viewer.raw("GET", "/api/v1" + route, expected=(403,))
        viewer.raw("GET", f"/api/v1/browse/media/{self.media['private']['id']}", expected=(404,))
        with self.session("direct") as session:
            record_resume_checkpoint(self.api, session["id"])
            viewer.raw("GET", session["url"], expected=(404,))
            saved = self.api.json("GET", f"/api/v1/browse/media/{self.media['direct']['id']}")
            check(saved["position_seconds"] == 8 and not saved["watched"], "Seek checkpoint incorrectly saved or marked watched")
            isolated = viewer.json("GET", f"/api/v1/browse/media/{self.media['direct']['id']}")
            check(isolated["position_seconds"] == 0 and not isolated["watched"], "Owner progress leaked to Viewer")
        with self.session("direct", position_seconds=None) as resumed:
            check(resumed["position_seconds"] == 8, "Owner resume checkpoint was not applied")
        with self.session("direct", api=viewer, position_seconds=None) as separate:
            check(separate["position_seconds"] == 0, "Viewer inherited Owner playback position")
        next_item = viewer.json("GET", f"/api/v1/browse/media/{self.media['episode1']['id']}/next")["item"]
        check(next_item and next_item["id"] == self.media["episode2"]["id"], "Next episode ordering is incorrect")
        viewer.raw("POST", "/api/v1/auth/logout", expected=(204,))
        return {"elapsed_playback_before_seek_seconds": 2.25, "checkpoint_seconds": 8,
                "resume_seconds": 8, "viewer_position_seconds": 0,
                "private_library_denied": True, "cross_user_stream_denied": True, "next_episode": 2}

    def source_loss(self) -> dict[str, Any]:
        source = self.paths["loss"]
        before = sha256(source)
        with self.session("loss") as session:
            with temporarily_missing(source, self.run_root, before):
                self.api.raw("GET", session["url"], expected=(404, 409))
                self.api.raw("GET", f"/api/v1/playback/{session['id']}", expected=(404, 409))
            self.api.raw("GET", session["url"], headers={"Range": "bytes=0-255"}, expected=(206,))
        return {"source_missing_failed_closed": True, "original_source_restored_sha256": before}

    def cleanup(self) -> dict[str, Any]:
        for session_id, client in list(self.sessions.items()):
            client.raw("POST", f"/api/v1/playback/{session_id}/stop", expected=(204, 404))
            self.sessions.pop(session_id, None)
        if self.viewer_id:
            self.api.json("PATCH", f"/api/v1/users/{self.viewer_id}", {"is_active": False})
        if self.original_policy is not None:
            restored = self.api.json("PATCH", "/api/v1/transcoding-policy", self.original_policy)
            check(restored == self.original_policy, "Original policy was not restored exactly")
        deadline = time.monotonic() + 15
        while True:
            streams = self.api.json("GET", "/api/v1/streams")
            health = streams["health"]
            if not streams["items"] and health["conversions"] == 0 and health["temp_bytes"] == 0:
                break
            check(time.monotonic() < deadline, "Owned stream/process/temp cleanup did not reach zero")
            time.sleep(0.5)
        return {"active_streams": 0, "conversions": 0, "temp_bytes": 0,
                "original_policy_restored": self.original_policy is not None, "test_viewer_disabled": bool(self.viewer_id)}

    def run(self) -> int:
        if self.case("native_instance_preflight", self.preflight) is None:
            return 1
        try:
            if self.case("generated_fixtures_worker_catalog", self.create_catalog) is None:
                return 1
            for name, method in (("direct_head_range_seek", self.direct), ("software_video_audio_remux", self.conversions),
                                 ("all_modes_and_verified_hardware", self.modes), ("audio_subtitle_and_hls_seek", self.tracks),
                                 ("progress_resume_viewer_isolation_next", self.progress_and_viewer),
                                 ("generated_source_loss_and_restore", self.source_loss)):
                self.case(name, method)
        finally:
            self.case("owned_stream_cleanup_and_policy_restore", self.cleanup)
        self.write_evidence()
        print("Evidence: " + str(self.output / "evidence.json"), flush=True)
        return int(any(row["status"] == "failed" for row in self.cases))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--allow-disposable-instance", action="store_true")
    result.add_argument("--base-url", default="http://127.0.0.1:18080")
    result.add_argument("--create-owner", action="store_true", help="Only for a fresh native instance with no Owner")
    result.add_argument("--credentials-file", type=Path, help="Existing private JSON file containing username/password")
    result.add_argument("--ffmpeg", type=Path, default=PROGRAM_ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe")
    result.add_argument("--output-dir", type=Path, default=REPOSITORY / "artifacts" / "development" / "acceptance")
    result.add_argument("--scan-timeout", type=int, default=180)
    return result


def main() -> int:
    try:
        return Harness(parser().parse_args()).run()
    except AcceptanceError as error:
        print("Acceptance refused: " + str(error), flush=True)
    except Exception as error:  # noqa: BLE001 - CLI boundary never prints credential-bearing exception details.
        print("Acceptance could not start: " + type(error).__name__ + "; sensitive details withheld", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
