---
name: visualize-local
description: Create temporary theme-aware interactive local HTML visualizations and return a named file link the user can open. Use when the user asks for interactive demos, calculators, dashboards, charts, slide-style presentations, mockups, HTML widgets, small games, step-through explainers, or says to visualize something locally without contaminating the workspace.
---

# Visualize Local

Create theme-aware self-contained HTML/CSS/JS visualizations in private hidden temp storage and return a friendly Markdown link to the generated file. This skill is for throwaway interactive artifacts, not repo deliverables.

## Workflow

1. Generate one complete HTML document with embedded CSS and JavaScript.
2. Do not write visualization files into the current workspace or repo unless the user explicitly asks for a repo artifact.
3. Save the HTML with the bundled writer script. Resolve `skill_dir` as the directory containing this `SKILL.md`; do not run scripts relative to the user workspace:

   ```bash
   python3 <skill_dir>/scripts/write_visualization.py --title "Calculator Demo" --slug calculator < generated.html
   ```

4. Parse the JSON output. The writer adds the required restrictive Content Security Policy before saving. Do not open the file yourself and do not start a server.
5. In the final response, include a named Markdown link to the absolute `html_path`, using the writer's `link_label` when possible.

## HTML Rules

- Produce a full document including `<!doctype html>`, `<html>`, `<head>`, and `<body>`.
- Embed all CSS and JavaScript directly in the file.
- Always create a theme-aware UI. Include `<meta name="color-scheme" content="light dark">`, set `color-scheme: light dark` in CSS, and define polished light and dark palettes with CSS variables plus `@media (prefers-color-scheme: dark)` or an equivalent explicit theme mechanism.
- Respect the user's explicit theme request when provided. Otherwise, let the page follow the viewer's system light/dark preference.
- Do not create a dark-only, light-only, beige, paper-white, pastel, or default browser-styled page unless the user explicitly asks for that theme.
- Avoid external network dependencies, CDNs, package managers, generated repo files, or asset downloads.
- Do not depend on frames, workers, objects, forms, WebRTC, or network connections; the writer's injected policy blocks them.
- Prefer plain HTML/CSS/JS unless the user specifically needs a framework-like artifact.
- Make the experience immediately usable: visible controls, clear state, keyboard support where natural, and no setup instructions inside the UI.
- Keep the page safe for direct local file use. Do not require a server, external assets, or browser automation to make the page work.

## Verification Rules

- Do not use browser automation or try to open the generated visualization.
- Before writing, make sure the generated HTML is complete and self-contained. The writer rejects missing document structure, missing `color-scheme`, and external dependency references, then injects a restrictive Content Security Policy as the first element in `<head>`.
- After writing, trust the writer's success output as the artifact check. If needed, use lightweight filesystem checks only, such as confirming the file exists.
- In the final response, always include the named Markdown link to the created HTML file and mention that the repo/workspace was not modified.

## Storage Contract

- The default output root is `.codex-visualize-local` under Python's system temp directory, as reported by `tempfile.gettempdir()`.
- Set `CODEX_VISUALIZE_LOCAL_ROOT` only when an explicit override is needed, such as isolated local tests.
- The output root must be private: directory permissions are `0700` and HTML file permissions are `0600`. The writer repairs user-owned unsafe directory permissions and refuses unsafe symlink/non-owned paths.
- A persistent `.writer.lock` file with `0600` permissions serializes filename allocation, writing, pruning, and success output across concurrent invocations.
- Each run writes one timestamped HTML file directly under the output root, using a cleaned slug in the filename to avoid collisions.
- Each writer invocation prunes old generated HTML files in that root so no more than 50 generated visualizations remain by default.
- Report the resulting local HTML file as a named Markdown link every time, but emphasize that the repo/workspace was not modified.

## Writer Script

Use `<skill_dir>/scripts/write_visualization.py` for all writes. It reads HTML from stdin and prints JSON:

```json
{
  "created_at": "2026-07-07T18:01:01.123456+00:00",
  "directory": "<system-temp>/.codex-visualize-local",
  "filename": "20260707-180101-123456-calculator.html",
  "html_path": "<system-temp>/.codex-visualize-local/20260707-180101-123456-calculator.html",
  "file_url": "file:///<system-temp>/.codex-visualize-local/20260707-180101-123456-calculator.html",
  "link_label": "Open Calculator Demo",
  "max_files": 50,
  "title": "Calculator Demo"
}
```
