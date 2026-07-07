#!/usr/bin/env python3
"""Write a self-contained HTML visualization into private temp storage."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from privacy import (
    PRIVATE_FILE_MODE,
    ensure_private_directory,
)


OUTPUT_DIR_NAME = ".codex-visualize-local"
OUTPUT_ROOT_ENV = "CODEX_VISUALIZE_LOCAL_ROOT"
MAX_SLUG_LENGTH = 64
DEFAULT_MAX_FILES = 50
OUTPUT_FILE_RE = re.compile(r"^\d{8}-\d{6}-\d{6}-[a-z0-9][a-z0-9._-]*\.html$")
REQUIRED_HTML_PATTERNS = {
    "doctype": re.compile(r"<!doctype\s+html\b", re.IGNORECASE),
    "html element": re.compile(r"<html\b", re.IGNORECASE),
    "head element": re.compile(r"<head\b", re.IGNORECASE),
    "body element": re.compile(r"<body\b", re.IGNORECASE),
    "color-scheme": re.compile(r"color-scheme", re.IGNORECASE),
}
DISALLOWED_HTML_PATTERNS = {
    "external script src": re.compile(r"<script\b[^>]*\bsrc\s*=", re.IGNORECASE),
    "external stylesheet link": re.compile(r"<link\b[^>]*\bhref\s*=", re.IGNORECASE),
    "remote src or href": re.compile(r"\b(?:src|href)\s*=\s*['\"]\s*https?://", re.IGNORECASE),
    "remote CSS url": re.compile(r"url\(\s*['\"]?\s*https?://", re.IGNORECASE),
}


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


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def output_root() -> Path:
    override = os.environ.get(OUTPUT_ROOT_ENV, "").strip()
    if override:
        return absolute_path(Path(override))
    return absolute_path(Path(tempfile.gettempdir()) / OUTPUT_DIR_NAME)


def file_url(path: Path) -> str:
    return "file://" + quote(str(path.resolve()))


def validate_html(html: str) -> list[str]:
    errors = [
        f"missing {name}"
        for name, pattern in REQUIRED_HTML_PATTERNS.items()
        if pattern.search(html) is None
    ]
    errors.extend(
        f"contains {name}"
        for name, pattern in DISALLOWED_HTML_PATTERNS.items()
        if pattern.search(html) is not None
    )
    return errors


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

    validation_errors = validate_html(html)
    if validation_errors:
        print(
            "error: invalid HTML: " + "; ".join(validation_errors),
            file=sys.stderr,
        )
        return 2

    root = output_root()
    try:
        ensure_private_directory(root, create=True)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    created_at = datetime.now(timezone.utc)
    html_path = unique_output_file(root, slug, created_at)

    write_private_text(html_path, html)
    prune_old_outputs(root, args.max_files)

    result = {
        "created_at": created_at.isoformat(),
        "directory": str(root),
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
