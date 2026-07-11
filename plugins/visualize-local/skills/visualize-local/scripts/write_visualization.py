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
URL_ATTRIBUTES = {
    "action",
    "archive",
    "attributionsrc",
    "background",
    "classid",
    "codebase",
    "data",
    "formaction",
    "href",
    "imagesrcset",
    "longdesc",
    "manifest",
    "ping",
    "poster",
    "src",
    "srcset",
    "usemap",
    "xlink:href",
}
SRCSET_ATTRIBUTES = {"imagesrcset", "srcset"}
ACTIVE_TRACKING_URL_ATTRIBUTES = {"attributionsrc", "ping"}
BLOCKED_EMBEDDED_ELEMENTS = {
    "applet",
    "embed",
    "fencedframe",
    "frame",
    "iframe",
    "object",
    "portal",
}
CSS_VALUE_ATTRIBUTES = {
    "clip-path",
    "cursor",
    "fill",
    "filter",
    "marker-end",
    "marker-mid",
    "marker-start",
    "marker",
    "mask",
    "shape-inside",
    "shape-outside",
    "shape-subtract",
    "stroke",
    "style",
}
CSS_STRING_URL_FUNCTIONS = ("-webkit-image-set", "image-set", "image", "src")
CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
COLOR_SCHEME_RE = re.compile(r"(?:^|[;{])\s*color-scheme\s*:", re.IGNORECASE)
CSS_WHITESPACE = " \t\n\f\r"
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
        self.has_color_scheme_meta = False
        self.has_color_scheme_css = False
        self.has_meta_refresh = False
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
        attributes: dict[str, str] = {}
        for name, value in attribute_items:
            attributes.setdefault(name, value)

        if self.head_end_offset is None and tag not in {"html", "head"}:
            self.elements_before_head.append(tag)

        if tag == "head" and self.head_end_offset is None:
            start_tag = self.get_starttag_text() or ""
            self.head_end_offset = self.absolute_offset() + len(start_tag)

        if tag == "style":
            self._style_depth += 1
        if tag == "meta":
            meta_name = attributes.get("name", "").strip().lower()
            content_tokens = attributes.get("content", "").lower().split()
            if meta_name == "color-scheme" and {"light", "dark"}.issubset(
                content_tokens
            ):
                self.has_color_scheme_meta = True
            if any(
                name == "http-equiv" and value.strip().lower() == "refresh"
                for name, value in attribute_items
            ):
                self.has_meta_refresh = True

        for attribute, value in attribute_items:
            if attribute in CSS_VALUE_ATTRIBUTES:
                self.style_sources.append(value)
            if attribute in URL_ATTRIBUTES:
                self.url_attributes.append((tag, attribute, value))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.style_sources.append(data)


def skip_css_comment(source: str, position: int) -> int:
    comment_end = source.find("*/", position + 2)
    return len(source) if comment_end < 0 else comment_end + 2


def skip_css_string(source: str, position: int) -> int:
    quote_character = source[position]
    position += 1
    while position < len(source):
        if source[position] == "\\":
            position += 2
        elif source[position] == quote_character:
            return position + 1
        else:
            position += 1
    return len(source)


def css_code_without_strings_and_comments(source: str) -> str:
    code = list(source)
    position = 0
    while position < len(source):
        if source.startswith("/*", position):
            end = skip_css_comment(source, position)
            code[position:end] = " " * (end - position)
            position = end
        elif source[position] in {'"', "'"}:
            end = skip_css_string(source, position)
            code[position:end] = " " * (end - position)
            position = end
        else:
            position += 1
    return "".join(code)


def skip_css_whitespace_and_comments(source: str, position: int) -> int:
    while position < len(source):
        if source[position] in CSS_WHITESPACE:
            position += 1
        elif source.startswith("/*", position):
            position = skip_css_comment(source, position)
        else:
            break
    return position


def is_css_name_character(character: str) -> bool:
    return (
        character.isalnum()
        or character in {"-", "_", "\\"}
        or ord(character) >= 0x80
    )


def css_url_values(source: str) -> list[str]:
    values: list[str] = []
    position = 0
    while position < len(source):
        if source.startswith("/*", position):
            position = skip_css_comment(source, position)
            continue
        if source[position] in {'"', "'"}:
            position = skip_css_string(source, position)
            continue

        previous_is_name = position > 0 and is_css_name_character(source[position - 1])
        if not previous_is_name and source[position : position + 3].lower() == "url":
            function_start = skip_css_whitespace_and_comments(source, position + 3)
            if function_start < len(source) and source[function_start] == "(":
                value_start = skip_css_whitespace_and_comments(source, function_start + 1)
                if value_start < len(source) and source[value_start] in {'"', "'"}:
                    value_end = skip_css_string(source, value_start)
                    values.append(
                        source[value_start + 1 : max(value_start + 1, value_end - 1)]
                    )
                    position = value_end
                else:
                    value_end = value_start
                    while value_end < len(source) and source[value_end] != ")":
                        if source[value_end] == "\\" and value_end + 1 < len(source):
                            value_end += 2
                        else:
                            value_end += 1
                    values.append(source[value_start:value_end].strip())
                    position = value_end + int(value_end < len(source))
                continue
        position += 1
    return values


def css_string_url_function_values(source: str) -> list[str]:
    values: list[str] = []
    position = 0
    while position < len(source):
        if source.startswith("/*", position):
            position = skip_css_comment(source, position)
            continue
        if source[position] in {'"', "'"}:
            position = skip_css_string(source, position)
            continue

        previous_is_name = position > 0 and is_css_name_character(source[position - 1])
        function_name = next(
            (
                name
                for name in CSS_STRING_URL_FUNCTIONS
                if source[position : position + len(name)].lower() == name
            ),
            None,
        )
        if previous_is_name or function_name is None:
            position += 1
            continue

        function_start = skip_css_whitespace_and_comments(
            source,
            position + len(function_name),
        )
        if function_start >= len(source) or source[function_start] != "(":
            position += 1
            continue

        position = function_start + 1
        nested_parentheses = 0
        while position < len(source):
            if source.startswith("/*", position):
                position = skip_css_comment(source, position)
            elif source[position] in {'"', "'"}:
                value_end = skip_css_string(source, position)
                if not nested_parentheses:
                    values.append(
                        source[position + 1 : max(position + 1, value_end - 1)]
                    )
                position = value_end
            elif source[position] == "(":
                nested_parentheses += 1
                position += 1
            elif source[position] == ")":
                if not nested_parentheses:
                    position += 1
                    break
                nested_parentheses -= 1
                position += 1
            else:
                position += 1
    return values


def inspect_html(html: str) -> HTMLDocumentInspector:
    inspector = HTMLDocumentInspector(html)
    inspector.feed(html)
    inspector.close()
    if any(
        COLOR_SCHEME_RE.search(css_code_without_strings_and_comments(source))
        for source in inspector.style_sources
    ):
        inspector.has_color_scheme_css = True
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


def markdown_link_label(title: str) -> str:
    printable_title = "".join(
        character if character.isprintable() else " " for character in title
    )
    label = re.sub(r"\s+", " ", f"Open {printable_title}").strip()
    return label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


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


def validate_inspector(inspector: HTMLDocumentInspector) -> list[str]:
    errors: list[str] = []
    if not inspector.has_doctype:
        errors.append("missing doctype")
    for element in ("html", "head", "body"):
        if element not in inspector.elements:
            errors.append(f"missing {element} element")
    if not inspector.has_color_scheme_meta:
        errors.append("missing color-scheme meta")
    if not inspector.has_color_scheme_css:
        errors.append("missing color-scheme CSS property")
    if inspector.elements_before_head:
        errors.append("contains element before head")
    if inspector.has_meta_refresh:
        errors.append("contains meta refresh")
    blocked_elements = sorted(inspector.elements & BLOCKED_EMBEDDED_ELEMENTS)
    if blocked_elements:
        errors.append("contains blocked embedded element: " + ", ".join(blocked_elements))

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
        if attribute not in SRCSET_ATTRIBUTES
    ]
    srcset_values = [
        value
        for _, attribute, value in inspector.url_attributes
        if attribute in SRCSET_ATTRIBUTES
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
    if any(
        attribute in ACTIVE_TRACKING_URL_ATTRIBUTES and bool(value.strip())
        for _, attribute, value in inspector.url_attributes
    ):
        errors.append("contains active tracking URL")

    css_code = [
        css_code_without_strings_and_comments(source)
        for source in inspector.style_sources
    ]
    if any(CSS_IMPORT_RE.search(source) for source in css_code):
        errors.append("contains external CSS import")
    css_urls = [
        value
        for source in inspector.style_sources
        for value in css_url_values(source) + css_string_url_function_values(source)
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
                "link_label": markdown_link_label(title),
                "max_files": args.max_files,
                "title": title,
            }
            print(json.dumps(result, sort_keys=True), flush=True)
    except OSError as error:
        print(f"error: output cannot be written safely: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
