"""Generate noncopyrighted local playback fixtures; outputs must stay ignored.

This is an opt-in development tool, never an application startup task.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "runtime/phase2-validation/media")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    movies, tv, private = output / "Movies", output / "TV", output / "Private"
    for directory in (movies, tv, private):
        directory.mkdir(exist_ok=True)

    def run(destination: Path, arguments: list[str]) -> None:
        if destination.exists():
            return
        subprocess.run([args.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-n", *arguments,
                        str(destination)], check=True, timeout=180)
        print(f"Generated {destination.name}")

    direct = movies / "Direct.Test.2026.mp4"
    run(direct, ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=90", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=90", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-profile:v", "main", "-level:v", "3.0", "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48",
        "-sc_threshold", "0", "-threads", "2", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart"])
    common = ["-i", str(direct), "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "-1"]
    run(movies / "Container.Test.2026.mkv", [*common, "-c", "copy"])
    run(movies / "Video.Conversion.2026.mkv", [*common, "-c:v", "mpeg4", "-q:v", "5", "-threads", "2", "-c:a", "copy"])
    run(movies / "Audio.Conversion.2026.mkv", [*common, "-c:v", "copy", "-c:a", "ac3", "-b:a", "192k"])
    run(movies / "Audio.Selection.2026.mkv", ["-i", str(direct), "-f", "lavfi", "-i",
        "sine=frequency=880:sample_rate=48000:duration=90", "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-metadata:s:a:0", "title=Low tone 440 Hz",
        "-metadata:s:a:1", "title=High tone 880 Hz", "-disposition:a:0", "default", "-disposition:a:1", "0"])
    captions = output / "generated-captions.srt"
    captions.write_text("1\n00:00:01,000 --> 00:00:20,000\nLocal captions: first cue.\n\n2\n00:00:25,000 --> 00:00:50,000\nLocal captions after seeking.\n", encoding="utf-8")
    run(movies / "Subtitle.Test.2026.mp4", ["-i", str(direct), "-i", str(captions), "-map", "0:v:0", "-map", "0:a:0",
        "-map", "1:s:0", "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
        "-movflags", "+faststart"])
    run(movies / "Subtitle.SRT.2026.mkv", ["-i", str(direct), "-i", str(captions), "-map", "0:v:0", "-map", "0:a:0",
        "-map", "1:s:0", "-c:v", "copy", "-c:a", "copy", "-c:s", "srt", "-metadata:s:s:0", "language=eng"])
    for number in (1, 2, 3):
        run(tv / f"Local.Series.S01E{number:02d}.Episode.{number}.mp4", [*common, "-t", "20", "-c", "copy", "-movflags", "+faststart"])
    run(private / "Private.Test.2026.mp4", [*common, "-t", "10", "-c", "copy", "-movflags", "+faststart"])
    run(movies / "Source.Loss.2026.mp4", [*common, "-c", "copy", "-movflags", "+faststart"])


if __name__ == "__main__":
    main()
