# Visualize Local Codex Plugin

Visualize Local packages the `$visualize-local` skill for Codex. It creates dark, self-contained HTML visualizations in private temp storage and opens them through a local preview server, without writing generated demos into the active project workspace.

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

The skill writes generated runs under `/private/tmp/codex-visualizations`, serves them from `127.0.0.1`, and auto-stops the local server after its TTL unless you explicitly ask otherwise.

## Contents

- `.agents/plugins/marketplace.json`: plugin marketplace catalog.
- `plugins/visualize-local`: Codex plugin package.
- `plugins/visualize-local/skills/visualize-local`: bundled skill and helper scripts.

## License

MIT
