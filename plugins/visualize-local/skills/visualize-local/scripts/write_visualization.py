#!/usr/bin/env python3
"""Write a self-contained HTML visualization into private temp storage."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from privacy import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    ensure_private_directory,
)


OUTPUT_ROOT = Path("/private/tmp/codex-visualizations")
CREATED_BY = "visualize-local"
MAX_SLUG_LENGTH = 64


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a temporary self-contained HTML visualization."
    )
    parser.add_argument("--title", required=True, help="Human-readable visualization title.")
    parser.add_argument("--slug", required=True, help="Short name used in the output directory.")
    parser.add_argument(
        "--prune-days",
        type=int,
        default=30,
        help="Delete visualize-local outputs older than this many days. Use 0 to disable.",
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


def prune_old_outputs(root: Path, prune_days: int) -> None:
    if prune_days <= 0 or not root.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=prune_days)
    for child in root.iterdir():
        if child.is_symlink():
            continue
        if not child.is_dir():
            continue

        manifest_path = child / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(manifest["created_at"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue

        if manifest.get("created_by") == CREATED_BY and created_at < cutoff:
            shutil.rmtree(child)


def unique_output_dir(root: Path, slug: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = root / f"{timestamp}-{slug}"
    suffix = 2
    while candidate.exists():
        candidate = root / f"{timestamp}-{slug}-{suffix}"
        suffix += 1
    return candidate


def main() -> int:
    args = parse_args()
    if args.prune_days < 0:
        print("error: --prune-days must be greater than or equal to 0", file=sys.stderr)
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

    prune_old_outputs(OUTPUT_ROOT, args.prune_days)

    output_dir = unique_output_dir(OUTPUT_ROOT, slug)
    output_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=False)
    output_dir.chmod(PRIVATE_DIR_MODE)

    html_path = output_dir / "index.html"
    manifest_path = output_dir / "manifest.json"
    created_at = datetime.now(timezone.utc).isoformat()

    write_private_text(html_path, html)
    write_private_text(
        manifest_path,
        json.dumps(
            {
                "created_by": CREATED_BY,
                "created_at": created_at,
                "title": title,
                "slug": slug,
                "html_file": "index.html",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    result = {
        "directory": str(output_dir),
        "html_path": str(html_path),
        "file_url": file_url(html_path),
        "title": title,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
