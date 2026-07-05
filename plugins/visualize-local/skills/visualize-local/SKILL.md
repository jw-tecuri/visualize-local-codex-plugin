---
name: visualize-local
description: Create temporary dark-themed interactive local HTML visualizations and automatically show them in the Codex in-app browser. Use when the user asks for interactive demos, calculators, dashboards, charts, slide-style presentations, mockups, HTML widgets, small games, step-through explainers, or says to visualize something locally without contaminating the workspace.
---

# Visualize Local

Create dark-themed self-contained HTML/CSS/JS visualizations in private temp storage and open them in the in-app browser. This skill is for throwaway interactive artifacts, not repo deliverables.

## Workflow

1. Generate one complete HTML document with embedded CSS and JavaScript.
2. Do not write visualization files into the current workspace or repo unless the user explicitly asks for a repo artifact.
3. Save the HTML with the bundled writer script. Resolve `skill_dir` as the directory containing this `SKILL.md`; do not run scripts relative to the user workspace:

   ```bash
   python3 <skill_dir>/scripts/write_visualization.py --title "Calculator Demo" --slug calculator < generated.html
   ```

4. Parse the JSON output and start the bundled localhost server helper for the generated directory:

   ```bash
   python3 <skill_dir>/scripts/serve_visualization.py /private/tmp/codex-visualizations/<run-dir>
   ```

   Keep the command session running while the user is viewing the visualization. By default, the helper exits after 3600 seconds. Use `--ttl-seconds 0` only when the user explicitly asks for an indefinite server.

5. Open the printed `local_url` in the Codex in-app browser:
   - load the Browser skill instructions first when available;
   - initialize/select the in-app browser;
   - call `await (await browser.capabilities.get("visibility")).set(true)`;
   - reuse `await browser.tabs.selected()` when it exists, otherwise call `await browser.tabs.new()`;
   - call `await tab.goto(localUrl)`;
   - call `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })`;
   - verify the page title or one visible UI element.
6. Do not try `file://` in the in-app browser by default. The writer still emits `file_url` as a useful artifact reference, but localhost is the default browser transport because this Codex app may block direct file navigation.

## HTML Rules

- Produce a full document including `<!doctype html>`, `<html>`, `<head>`, and `<body>`.
- Embed all CSS and JavaScript directly in the file.
- Always create a dark-themed UI. Include `<meta name="color-scheme" content="dark">`, set `color-scheme: dark` in CSS, use a dark page background, light foreground text, and visible focus/hover states.
- Do not create light, beige, paper-white, pastel, or default browser-styled pages unless the user explicitly overrides the theme.
- Avoid external network dependencies, CDNs, package managers, generated repo files, or asset downloads.
- Prefer plain HTML/CSS/JS unless the user specifically needs a framework-like artifact.
- Make the experience immediately usable: visible controls, clear state, keyboard support where natural, and no setup instructions inside the UI.
- Keep the page safe for local browser use. Use the bundled localhost server for in-app browser display.

## Verification Rules

- Verify ordinary HTML visualizations with a small `evaluate` check for title, heading, controls, counters, or status text.
- For canvas/WebGL/SVG-heavy visualizations, expose verification-friendly DOM state such as a status element, counters, selected mode text, or `data-*` attributes.
- Do not call canvas rendering APIs such as `getContext("2d")`, `getImageData(...)`, or WebGL pixel reads inside the browser read-only `evaluate` layer.
- Prefer a screenshot plus DOM-visible controls for visual smoke checks of canvas/WebGL work.
- If `domSnapshot()` fails in this browser build, fall back to a targeted `evaluate` check and `tab.screenshot(...)`.
- In the final response, include the localhost URL and mention that the local server session must remain running while the user views the page, and that it auto-stops after the reported TTL.

## Storage Contract

- The only default output root is `/private/tmp/codex-visualizations`.
- The output root and run directories must be private: directories are `0700`, HTML and manifest files are `0600`. The writer and server share the same privacy checks, repair user-owned unsafe permissions, require both `index.html` and `manifest.json`, and refuse unsafe symlink/non-owned paths.
- Each run goes into a unique timestamped directory containing:
  - `index.html`
  - `manifest.json`
- The writer may prune only old directories it created under that output root.
- Report the resulting local file path or URL to the user, but emphasize that the repo/workspace was not modified.

## Writer Script

Use `<skill_dir>/scripts/write_visualization.py` for all writes. It reads HTML from stdin and prints JSON:

```json
{
  "directory": "/private/tmp/codex-visualizations/...",
  "html_path": "/private/tmp/codex-visualizations/.../index.html",
  "file_url": "file:///private/tmp/codex-visualizations/.../index.html",
  "title": "Calculator Demo"
}
```

Use `<skill_dir>/scripts/serve_visualization.py` to show an already-written visualization in the in-app browser. It verifies and repairs the storage privacy contract before serving, prints JSON with `local_url` and `ttl_seconds`, then serves the directory until the command session is stopped or the TTL expires. The helper uses a threaded local server so one slow or stale browser connection does not block later requests.
