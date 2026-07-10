# Visualize Local Codex Plugin

Visualize Local packages the `$visualize-local` skill for Codex. It creates theme-aware, self-contained HTML visualizations in a private hidden directory under the system temp folder and returns a friendly file link, without writing generated demos into the active project workspace. The writer injects a restrictive Content Security Policy that blocks network connections and advanced embedded execution surfaces while preserving inline HTML/CSS/JS and embedded media.

## Install

Add this repository as a Codex plugin marketplace:

```bash
codex plugin marketplace add jw-tecuri/visualize-local-codex-plugin
```

Then open the Codex plugin directory and install **Visualize Local** from the `visualize-local-codex-plugin` marketplace.

## Use

Start a new Codex thread after installing, then ask:

```text
Use $visualize-local to create an interactive calculator demo.
```

The skill writes generated files under `.codex-visualize-local` in the system temp folder, using timestamped filenames to avoid collisions. Set `CODEX_VISUALIZE_LOCAL_ROOT` to override that directory. Writer operations are serialized with a private `.writer.lock` file, and each invocation prunes the hidden directory so no more than 50 generated HTML visualizations remain. Final responses include a named Markdown link to the generated file so you can open it yourself in the in-app browser.

## Contents

- `.agents/plugins/marketplace.json`: plugin marketplace catalog.
- `plugins/visualize-local`: Codex plugin package.
- `plugins/visualize-local/skills/visualize-local`: bundled skill and writer script.

## License

MIT
