from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
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
    def run_writer(
        self,
        root,
        html: str = VALID_HTML,
        *,
        slug: str = "writer test",
        max_files: int = 50,
        cwd=None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODEX_VISUALIZE_LOCAL_ROOT"] = str(root)
        return subprocess.run(
            [
                sys.executable,
                str(WRITER),
                "--title",
                "Writer Test",
                "--slug",
                slug,
                "--max-files",
                str(max_files),
            ],
            input=html,
            text=True,
            capture_output=True,
            env=env,
            cwd=cwd,
            check=False,
        )

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
