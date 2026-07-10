#!/usr/bin/env python3
"""Write a self-contained HTML visualization into private temp storage."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from privacy import (
    PRIVATE_FILE_MODE,
    ensure_private_directory,
)


OUTPUT_DIR_NAME = ".codex-visualize-local"
OUTPUT_ROOT_ENV = "CODEX_VISUALIZE_LOCAL_ROOT"
LOCK_FILE_NAME = ".writer.lock"
MAX_SLUG_LENGTH = 64
DEFAULT_MAX_FILES = 50
OUTPUT_FILE_RE = re.compile(r"^\d{8}-\d{6}-\d{6}-[a-z0-9][a-z0-9._-]*\.html$")
URL_ATTRIBUTES = {"src", "href", "srcset", "poster", "data", "action", "formaction"}
CSS_URL_RE = re.compile(
    r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)]*))\s*\)",
    re.IGNORECASE,
)
CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
COLOR_SCHEME_RE = re.compile(r"\bcolor-scheme\s*:", re.IGNORECASE)
CSP_POLICY = "; ".join(
    (
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "font-src data:",
        "media-src data:",
        "connect-src 'none'",
        "manifest-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "child-src 'none'",
        "worker-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "webrtc 'block'",
    )
)
CSP_META = (
    '<meta http-equiv="Content-Security-Policy" '
    f'content="{CSP_POLICY}">'
)


class HTMLDocumentInspector(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.line_offsets = [0]
        self.line_offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self.has_doctype = False
        self.elements: set[str] = set()
        self.has_color_scheme = False
        self.head_end_offset: int | None = None
        self.elements_before_head: list[str] = []
        self.url_attributes: list[tuple[str, str, str]] = []
        self.style_sources: list[str] = []
        self._style_depth = 0

    def absolute_offset(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() == "doctype html":
            self.has_doctype = True

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        self.elements.add(tag)
        attribute_items = [(name.lower(), value or "") for name, value in attrs]
        attributes = dict(attribute_items)

        if self.head_end_offset is None and tag not in {"html", "head"}:
            self.elements_before_head.append(tag)

        if tag == "head" and self.head_end_offset is None:
            start_tag = self.get_starttag_text() or ""
            self.head_end_offset = self.absolute_offset() + len(start_tag)

        if tag == "style":
            self._style_depth += 1
        if tag == "meta" and attributes.get("name", "").lower() == "color-scheme":
            self.has_color_scheme = True

        for attribute, value in attribute_items:
            if attribute == "style":
                self.style_sources.append(value)
            if attribute in URL_ATTRIBUTES:
                self.url_attributes.append((tag, attribute, value))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.style_sources.append(data)


def inspect_html(html: str) -> HTMLDocumentInspector:
    inspector = HTMLDocumentInspector(html)
    inspector.feed(html)
    inspector.close()
    if any(COLOR_SCHEME_RE.search(source) for source in inspector.style_sources):
        inspector.has_color_scheme = True
    return inspector


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


def is_remote_url_reference(value: str) -> bool:
    value = value.strip().lower()
    if value.startswith("data:"):
        return False
    return value.startswith(("http://", "https://", "//")) or bool(
        re.search(r"(?:^|[\s,])(?:https?:)?//", value)
    )


def is_embedded_url_reference(value: str) -> bool:
    value = value.strip()
    return (
        not value
        or value.startswith("#")
        or value.lower().startswith("data:")
        or value.lower() == "about:blank"
    )


def srcset_urls(value: str) -> list[str]:
    """Extract URL tokens while preserving commas inside data URLs."""
    urls: list[str] = []
    position = 0
    whitespace = " \t\n\f\r"

    while position < len(value):
        while position < len(value) and value[position] in whitespace + ",":
            position += 1
        if position >= len(value):
            break

        url_start = position
        while position < len(value) and value[position] not in whitespace:
            position += 1
        raw_url = value[url_start:position]
        url = raw_url.rstrip(",")
        if url:
            urls.append(url)
        if len(url) != len(raw_url):
            continue

        parenthesis_depth = 0
        while position < len(value):
            character = value[position]
            position += 1
            if character == "(":
                parenthesis_depth += 1
            elif character == ")" and parenthesis_depth:
                parenthesis_depth -= 1
            elif character == "," and not parenthesis_depth:
                break

    return urls


def matched_css_url(match: re.Match[str]) -> str:
    return next(value for value in match.groups() if value is not None).strip()


def validate_inspector(inspector: HTMLDocumentInspector) -> list[str]:
    errors: list[str] = []
    if not inspector.has_doctype:
        errors.append("missing doctype")
    for element in ("html", "head", "body"):
        if element not in inspector.elements:
            errors.append(f"missing {element} element")
    if not inspector.has_color_scheme:
        errors.append("missing color-scheme")
    if inspector.elements_before_head:
        errors.append("contains element before head")

    if any(
        tag == "script" and attribute == "src"
        for tag, attribute, _ in inspector.url_attributes
    ):
        errors.append("contains external script src")
    if any(
        tag == "link" and attribute == "href"
        for tag, attribute, _ in inspector.url_attributes
    ):
        errors.append("contains external stylesheet link")

    ordinary_urls = [
        value
        for _, attribute, value in inspector.url_attributes
        if attribute != "srcset"
    ]
    srcset_values = [
        value
        for _, attribute, value in inspector.url_attributes
        if attribute == "srcset"
    ]
    parsed_srcsets = [(value, srcset_urls(value)) for value in srcset_values]
    if any(is_remote_url_reference(value) for value in ordinary_urls) or any(
        is_remote_url_reference(candidate)
        for _, candidates in parsed_srcsets
        for candidate in candidates
    ):
        errors.append("contains remote URL attribute")
    if any(
        not is_embedded_url_reference(value) for value in ordinary_urls
    ) or any(
        bool(value.strip())
        and (
            not candidates
            or any(not candidate.lower().startswith("data:") for candidate in candidates)
        )
        for value, candidates in parsed_srcsets
    ):
        errors.append("contains non-embedded URL reference")

    if any(CSS_IMPORT_RE.search(source) for source in inspector.style_sources):
        errors.append("contains external CSS import")
    css_urls = [
        matched_css_url(match)
        for source in inspector.style_sources
        for match in CSS_URL_RE.finditer(source)
    ]
    if any(is_remote_url_reference(value) for value in css_urls):
        errors.append("contains remote CSS url")
    if any(not is_embedded_url_reference(value) for value in css_urls):
        errors.append("contains non-embedded URL reference")
    return errors


def validate_html(html: str) -> list[str]:
    return validate_inspector(inspect_html(html))


def inject_content_security_policy(html: str, inspector: HTMLDocumentInspector) -> str:
    if inspector.head_end_offset is None:
        raise ValueError("cannot inject Content Security Policy without a head element")
    offset = inspector.head_end_offset
    return html[:offset] + "\n  " + CSP_META + html[offset:]


@contextmanager
def exclusive_output_lock(root: Path) -> Iterator[Path]:
    lock_path = root / LOCK_FILE_NAME
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield lock_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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


def prune_old_outputs(
    root: Path,
    max_files: int,
    *,
    protected_path: Path | None = None,
) -> None:
    if max_files <= 0 or not root.exists():
        return

    generated_files = generated_html_files(root)
    protected_file_count = int(protected_path in generated_files)
    other_files = [path for path in generated_files if path != protected_path]
    keep_other_files = max(0, max_files - protected_file_count)

    for old_file in sort_newest_first(other_files)[keep_other_files:]:
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
        inspector = inspect_html(html)
    except Exception as error:
        print(f"error: invalid HTML: cannot parse document: {error}", file=sys.stderr)
        return 2

    validation_errors = validate_inspector(inspector)
    if validation_errors:
        print(
            "error: invalid HTML: " + "; ".join(validation_errors),
            file=sys.stderr,
        )
        return 2

    try:
        html = inject_content_security_policy(html, inspector)
    except ValueError as error:
        print(f"error: invalid HTML: {error}", file=sys.stderr)
        return 2

    root = output_root()
    try:
        ensure_private_directory(root, create=True)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        with exclusive_output_lock(root):
            created_at = datetime.now(timezone.utc)
            html_path = unique_output_file(root, slug, created_at)

            write_private_text(html_path, html)
            prune_old_outputs(root, args.max_files, protected_path=html_path)

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
    except OSError as error:
        print(f"error: output cannot be written safely: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
