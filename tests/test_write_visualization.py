from __future__ import annotations

import fcntl
import json
import os
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "plugins/visualize-local/skills/visualize-local/scripts"
WRITER = SCRIPT_DIR / "write_visualization.py"
sys.path.insert(0, str(SCRIPT_DIR))

import write_visualization  # noqa: E402


VALID_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="light dark">
  <title>Writer Test</title>
  <style>:root { color-scheme: light dark; }</style>
</head>
<body><h1>Writer Test</h1></body>
</html>
"""


class WriteVisualizationTests(unittest.TestCase):
    def writer_command(
        self,
        *,
        title: str = "Writer Test",
        slug: str = "writer test",
        max_files: int = 50,
    ) -> list[str]:
        return [
            sys.executable,
            str(WRITER),
            "--title",
            title,
            "--slug",
            slug,
            "--max-files",
            str(max_files),
        ]

    def run_writer(
        self,
        root,
        html: str = VALID_HTML,
        *,
        title: str = "Writer Test",
        slug: str = "writer test",
        max_files: int = 50,
        cwd=None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODEX_VISUALIZE_LOCAL_ROOT"] = str(root)
        return subprocess.run(
            self.writer_command(title=title, slug=slug, max_files=max_files),
            input=html,
            text=True,
            capture_output=True,
            env=env,
            cwd=cwd,
            check=False,
        )

    def start_writer(
        self,
        root: Path,
        *,
        slug: str,
        max_files: int,
    ) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["CODEX_VISUALIZE_LOCAL_ROOT"] = str(root)
        process = subprocess.Popen(
            self.writer_command(slug=slug, max_files=max_files),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert process.stdin is not None
        process.stdin.write(VALID_HTML)
        process.stdin.close()
        process.stdin = None
        return process

    def test_writes_private_html_with_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, slug="Writer Test!")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            html_path = Path(payload["html_path"])
            self.assertEqual(payload["directory"], str(root))
            self.assertEqual(payload["filename"], html_path.name)
            self.assertEqual(payload["link_label"], "Open Writer Test")
            self.assertEqual(payload["max_files"], 50)
            self.assertEqual(html_path.parent, root)
            self.assertTrue(html_path.name.endswith("-writer-test.html"))
            self.assertTrue(payload["file_url"].startswith("file://"))
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(html_path.stat().st_mode), 0o600)
            lock_path = root / write_visualization.LOCK_FILE_NAME
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_escapes_markdown_link_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, title="Bad]\n[Title\\\x1b")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["link_label"], "Open Bad\\] \\[Title\\\\")

    def test_rejects_additional_external_url_attributes(self) -> None:
        external_references = {
            "SVG xlink": '<svg><image xlink:href="https://example.com/a.png"></image></svg>',
            "ping": '<a href="#target" ping="https://example.com/ping">Target</a>',
            "attribution source": (
                '<img src="data:image/gif;base64,AA==" '
                'attributionsrc="https://example.com/register">'
            ),
            "background": '<table background="missing.png"></table>',
            "image srcset": (
                '<link rel="preload" imagesrcset="https://example.com/a.png 1x">'
            ),
        }

        for label, markup in external_references.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / ".codex-visualize-local"
                html = VALID_HTML.replace("</body>", markup + "</body>")

                result = self.run_writer(root, html)

                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid HTML", result.stderr)
                self.assertFalse(root.exists())

    def test_injects_restrictive_csp_as_first_head_child(self) -> None:
        html = VALID_HTML.replace(
            "</body>",
            '<script>fetch("https://example.com/data.json")</script></body>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            written_html = Path(payload["html_path"]).read_text(encoding="utf-8")
            inspector = write_visualization.inspect_html(written_html)
            self.assertIsNotNone(inspector.head_end_offset)
            head_content = written_html[inspector.head_end_offset :].lstrip()
            self.assertTrue(head_content.startswith(write_visualization.CSP_META))
            for directive in (
                "default-src 'none'",
                "connect-src 'none'",
                "frame-src 'none'",
                "worker-src 'none'",
                "form-action 'none'",
                "webrtc 'block'",
            ):
                self.assertIn(directive, head_content)
            self.assertIn('fetch("https://example.com/data.json")', written_html)

    def test_injected_csp_preserves_existing_policy(self) -> None:
        existing_policy = (
            '<meta http-equiv="Content-Security-Policy" content="default-src data:">'
        )
        html = VALID_HTML.replace("<meta charset", existing_policy + "<meta charset")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            written_html = Path(payload["html_path"]).read_text(encoding="utf-8")
            self.assertEqual(written_html.count("Content-Security-Policy"), 2)
            self.assertIn(existing_policy, written_html)

    def test_rejects_meta_refresh(self) -> None:
        refresh = '<meta http-equiv="refresh" content="0; url=https://example.com">'
        html = VALID_HTML.replace("<meta charset", refresh + "<meta charset")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("meta refresh", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_elements_blocked_by_csp(self) -> None:
        blocked_markup = {
            "iframe": '<iframe src="about:blank"></iframe>',
            "object": '<object data="data:text/plain,example"></object>',
            "embed": '<embed src="data:text/plain,example">',
        }

        for label, markup in blocked_markup.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / ".codex-visualize-local"
                html = VALID_HTML.replace("</body>", markup + "</body>")

                result = self.run_writer(root, html)

                self.assertEqual(result.returncode, 2)
                self.assertIn("blocked embedded element", result.stderr)
                self.assertFalse(root.exists())

    def test_requires_valid_color_scheme_meta_and_css_property(self) -> None:
        cases = {
            "custom property": VALID_HTML.replace(
                '<meta name="color-scheme" content="light dark">',
                "",
            ).replace("color-scheme: light dark", "--color-scheme: light dark"),
            "invalid meta content": VALID_HTML.replace(
                'content="light dark"',
                'content="light"',
            ),
            "missing CSS property": VALID_HTML.replace(
                "color-scheme: light dark",
                "--color-scheme: light dark",
            ),
            "supports query only": VALID_HTML.replace(
                "color-scheme: light dark",
                "@supports (color-scheme: light dark) {}",
            ),
        }

        for label, html in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / ".codex-visualize-local"

                result = self.run_writer(root, html)

                self.assertEqual(result.returncode, 2)
                self.assertIn("missing color-scheme", result.stderr)
                self.assertFalse(root.exists())

    def test_relative_override_returns_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp).resolve()
            expected_root = cwd / ".relative-visualizations"

            result = self.run_writer(".relative-visualizations", cwd=cwd)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            html_path = Path(payload["html_path"])
            self.assertEqual(Path(payload["directory"]), expected_root)
            self.assertTrue(html_path.is_absolute())
            self.assertEqual(html_path.parent, expected_root)
            self.assertEqual(stat.S_IMODE(expected_root.stat().st_mode), 0o700)

    def test_repairs_user_owned_directory_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"
            root.mkdir()
            root.chmod(0o755)

            result = self.run_writer(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_rejects_symlink_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            target = parent / "target"
            target.mkdir()
            root = parent / ".codex-visualize-local"
            root.symlink_to(target, target_is_directory=True)

            result = self.run_writer(root)

            self.assertEqual(result.returncode, 2)
            self.assertIn("must not be a symlink", result.stderr)

    def test_rejects_symlink_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"
            root.mkdir(mode=0o700)
            target = Path(temp) / "lock-target"
            target.write_text("unchanged", encoding="utf-8")
            (root / write_visualization.LOCK_FILE_NAME).symlink_to(target)

            result = self.run_writer(root)

            self.assertEqual(result.returncode, 2)
            self.assertIn("cannot be written safely", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_rejects_executable_content_before_head(self) -> None:
        html = VALID_HTML.replace(
            "<html>",
            '<html><script>fetch("https://example.com/data.json")</script>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("element before head", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_remote_duplicate_url_attribute(self) -> None:
        html = VALID_HTML.replace(
            "</body>",
            '<img src="https://example.com/a.png" src="data:image/gif;base64,AA==">'
            "</body>",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("remote URL attribute", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_incomplete_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, "<html></html>")

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid HTML", result.stderr)
            self.assertIn("missing doctype", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_external_dependencies(self) -> None:
        html = VALID_HTML.replace("</head>", '<script src="app.js"></script></head>')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("external script src", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_protocol_relative_urls(self) -> None:
        html = VALID_HTML.replace("</body>", '<img src="//example.com/a.png"></body>')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("remote URL attribute", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_remote_srcset(self) -> None:
        html = VALID_HTML.replace(
            "</body>",
            '<img srcset="local.png 1x, https://example.com/a.png 2x"></body>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("remote URL attribute", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_unquoted_remote_srcset_candidate(self) -> None:
        html = VALID_HTML.replace(
            "</body>",
            '<img srcset=local.png,https://example.com/a.png></body>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("remote URL attribute", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_css_import(self) -> None:
        html = VALID_HTML.replace(
            "</head>",
            '<style>@import "https://example.com/a.css";</style></head>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("external CSS import", result.stderr)
            self.assertFalse(root.exists())

    def test_rejects_local_asset_references(self) -> None:
        local_references = {
            "image source": '<img src="missing.png">',
            "video poster": '<video poster="missing.jpg"></video>',
            "CSS URL": '<div style="background-image: url(missing.png)"></div>',
            "quoted remote CSS URL": (
                '<div style="background-image: url(\'https://example.com/a.png\')"></div>'
            ),
            "CSS image set": (
                '<div style="background-image: image-set(\'missing.png\' 1x)"></div>'
            ),
            "CSS image function": (
                '<div style="background-image: image(\'missing.png\')"></div>'
            ),
            "SVG presentation URL": (
                '<svg><rect fill="url(https://example.com/pattern.svg)"></rect></svg>'
            ),
        }

        for label, markup in local_references.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / ".codex-visualize-local"
                html = VALID_HTML.replace("</body>", f"{markup}</body>")

                result = self.run_writer(root, html)

                self.assertEqual(result.returncode, 2)
                self.assertIn("non-embedded URL reference", result.stderr)
                self.assertFalse(root.exists())

    def test_ignores_dependency_syntax_in_non_markup_content(self) -> None:
        examples = (
            '<pre>&lt;img src="missing.png"&gt; url(missing.png)</pre>'
            '<script>const sample = `<img src="missing.png">`; '
            'const css = "url(missing.png)";</script>'
            '<!-- <img src="missing.png"><style>div{background:url(missing.png)}</style> -->'
        )
        html = VALID_HTML.replace("</body>", examples + "</body>")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(Path(payload["html_path"]).exists())

    def test_ignores_dependency_tokens_in_css_strings_and_comments(self) -> None:
        css = (
            '<style>.label::after{content:"@import url(missing.png)"}'
            "/* @import url(missing.png); */</style>"
        )
        html = VALID_HTML.replace("</head>", css + "</head>")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(Path(payload["html_path"]).exists())

    def test_allows_embedded_asset_references(self) -> None:
        html = VALID_HTML.replace(
            "</body>",
            '<a href="#details">Details</a>'
            '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">'
            '<svg><use href="#icon"></use></svg>'
            '<div style="mask-image: url(#icon)"></div>'
            '<div style="background-image: url(\'data:image/gif;base64,AA==\')"></div>'
            '<div style="background-image: image-set(\'data:image/gif;base64,AA==\' 1x)"></div>'
            '<div style="background-image: image(\'data:image/gif;base64,AA==\')"></div>'
            '<svg><rect fill="url(#icon)"></rect></svg>'
            "</body>",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(Path(payload["html_path"]).exists())

    def test_allows_embedded_srcset_candidates(self) -> None:
        first = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        second = "data:image/gif;base64,R0lGODlhAQABAIAAAAD///8AAP///ywAAAAAAQABAAACAUwAOw=="
        html = VALID_HTML.replace(
            "</body>",
            f'<img srcset="{first} 1x, {second} 2x"></body>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_mixed_embedded_and_local_srcset_candidates(self) -> None:
        embedded = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        html = VALID_HTML.replace(
            "</body>",
            f'<img srcset="{embedded} 1x, missing.png 2x"></body>',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"

            result = self.run_writer(root, html)

            self.assertEqual(result.returncode, 2)
            self.assertIn("non-embedded URL reference", result.stderr)
            self.assertFalse(root.exists())

    def test_writer_operations_are_serialized_by_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"
            root.mkdir(mode=0o700)
            lock_path = root / write_visualization.LOCK_FILE_NAME
            lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            processes: list[subprocess.Popen[str]] = []
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                processes = [
                    self.start_writer(root, slug=f"concurrent-{index}", max_files=1)
                    for index in range(2)
                ]
                time.sleep(0.2)
                self.assertTrue(all(process.poll() is None for process in processes))
                self.assertEqual(write_visualization.generated_html_files(root), [])
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

            payloads = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                payloads.append(json.loads(stdout))

            remaining = write_visualization.generated_html_files(root)
            self.assertEqual(len(remaining), 1)
            self.assertIn(remaining[0].name, {payload["filename"] for payload in payloads})
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_success_output_is_flushed_before_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"
            env = os.environ.copy()
            env["CODEX_VISUALIZE_LOCAL_ROOT"] = str(root)
            wrapper = (
                f"import sys,time; sys.path.insert(0,{str(SCRIPT_DIR)!r}); "
                "import write_visualization as writer; "
                "returncode=writer.main(); time.sleep(2); raise SystemExit(returncode)"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    wrapper,
                    "--title",
                    "Writer Test",
                    "--slug",
                    "flush-test",
                    "--max-files",
                    "1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(VALID_HTML)
            process.stdin.close()
            process.stdin = None

            readable = select.select([process.stdout], [], [], 1)[0]
            self.assertTrue(readable)
            self.assertIsNone(process.poll())
            payload = json.loads(process.stdout.readline())
            _, stderr = process.communicate(timeout=5)

            self.assertEqual(process.returncode, 0, stderr)
            self.assertTrue(Path(payload["html_path"]).exists())

    def test_pruning_never_deletes_the_current_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex-visualize-local"
            root.mkdir()
            future_file = root / "99991231-235959-999999-future.html"
            future_file.write_text("future", encoding="utf-8")

            result = self.run_writer(root, max_files=1)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            html_path = Path(payload["html_path"])
            self.assertTrue(html_path.exists())
            self.assertFalse(future_file.exists())
            self.assertEqual(len(list(root.glob("*.html"))), 1)

    def test_prune_keeps_newest_generated_files_and_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                (root / f"20260707-18010{index}-000000-prune-test.html").write_text(
                    str(index),
                    encoding="utf-8",
                )
            unrelated = root / "notes.html"
            unrelated.write_text("keep me", encoding="utf-8")

            write_visualization.prune_old_outputs(root, 3)

            remaining = sorted(
                path.name
                for path in root.glob("*.html")
                if write_visualization.OUTPUT_FILE_RE.match(path.name)
            )
            self.assertEqual(
                remaining,
                [
                    "20260707-180102-000000-prune-test.html",
                    "20260707-180103-000000-prune-test.html",
                    "20260707-180104-000000-prune-test.html",
                ],
            )
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
