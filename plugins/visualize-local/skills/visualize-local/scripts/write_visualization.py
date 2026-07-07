#!/usr/bin/env python3
"""Write a self-contained HTML visualization into private temp storage."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from privacy import (
    PRIVATE_FILE_MODE,
    ensure_private_directory,
)


OUTPUT_ROOT = Path("/private/tmp/.codex-visualize-local")
MAX_SLUG_LENGTH = 64
DEFAULT_MAX_FILES = 50
OUTPUT_FILE_RE = re.compile(r"^\d{8}-\d{6}-\d{6}-[a-z0-9][a-z0-9._-]*\.html$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a temporary self-contained HTML visualization."
    )
    parser.add_argument("--title", required=True, help="Human-readable visualization title.")
    parser.add_argument("--slug", required=True, help="Short name used in the output filename.")
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="Keep at most this many generated HTML files. Use 0 to disable pruning.",
    )
    return parser.parse_args()


def clean_slug(raw_slug: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_slug.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    if not slug:
        raise ValueError("--slug must contain at least one letter or digit")
    return slug[:MAX_SLUG_LENGTH].strip("-._") or "visualization"


def file_url(path: Path) -> str:
    return "file://" + quote(str(path.resolve()))


def write_private_text(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    path.chmod(PRIVATE_FILE_MODE)


def generated_html_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for child in root.iterdir():
        if child.is_symlink() or not child.is_file():
            continue
        if OUTPUT_FILE_RE.match(child.name):
            files.append(child)
    return files


def sort_newest_first(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda path: path.name, reverse=True)


def prune_old_outputs(root: Path, max_files: int) -> None:
    if max_files <= 0 or not root.exists():
        return

    for old_file in sort_newest_first(generated_html_files(root))[max_files:]:
        try:
            old_file.unlink()
        except OSError:
            continue


def unique_output_file(root: Path, slug: str, now: datetime) -> Path:
    timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
    candidate = root / f"{timestamp}-{slug}.html"
    suffix = 2
    while candidate.exists():
        candidate = root / f"{timestamp}-{slug}-{suffix}.html"
        suffix += 1
    return candidate


def main() -> int:
    args = parse_args()
    if args.max_files < 0:
        print("error: --max-files must be greater than or equal to 0", file=sys.stderr)
        return 2

    title = args.title.strip()
    if not title:
        print("error: --title must not be empty", file=sys.stderr)
        return 2

    try:
        slug = clean_slug(args.slug)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    html = sys.stdin.read()
    if not html.strip():
        print("error: HTML stdin must not be empty", file=sys.stderr)
        return 2

    try:
        ensure_private_directory(OUTPUT_ROOT, create=True)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    created_at = datetime.now(timezone.utc)
    html_path = unique_output_file(OUTPUT_ROOT, slug, created_at)

    write_private_text(html_path, html)
    prune_old_outputs(OUTPUT_ROOT, args.max_files)

    result = {
        "created_at": created_at.isoformat(),
        "directory": str(OUTPUT_ROOT),
        "filename": html_path.name,
        "html_path": str(html_path),
        "file_url": file_url(html_path),
        "link_label": f"Open {title}",
        "max_files": args.max_files,
        "title": title,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
