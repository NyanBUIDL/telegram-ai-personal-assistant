"""Stage only count summaries and known synthetic art images; never copy logs."""

import argparse
import json
import shutil
from pathlib import Path

COUNTS = {"passed", "failed", "skipped", "errors", "exit_code"}
IMAGES = {
    *(f"browser-{kind}-{width}.png" for kind in ("components", "login") for width in (360, 390, 1280, 1440)),
    "browser-dialog-390.png",
    *(f"d02-browser-{width}.png" for width in (360, 390, 1280, 1440)),
    "native-backup-dialog.png",
    *(f"native-dialog-scale-{scale}.png" for scale in ("1", "1.25", "1.5", "2")),
}


def stage(source: Path, target: Path) -> None:
    if source.is_symlink() or target.is_symlink() or target.exists():
        raise ValueError("artifact directories must be real and output must be fresh")
    source = source.resolve(strict=True)
    files = []
    for name in sorted({"summary.json", *IMAGES}):
        value = source / name
        if not value.exists():
            continue
        if value.is_symlink() or not value.is_file() or value.resolve().parent != source:
            raise ValueError("unsafe artifact source")
        if name == "summary.json":
            counts = json.loads(value.read_text(encoding="utf-8"))
            if not isinstance(counts, dict) or set(counts) != COUNTS or any(type(v) is not int or v < 0 for v in counts.values()):
                raise ValueError("unsafe artifact summary")
        elif value.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("unsafe artifact image")
        files.append(value)
    if not files:
        raise ValueError("no approved evidence found")
    target.mkdir(parents=True)
    for value in files:
        shutil.copyfile(value, target / value.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        stage(args.source, args.target)
    except (ValueError, OSError, json.JSONDecodeError):
        raise SystemExit("unsafe or missing CI artifact evidence") from None
