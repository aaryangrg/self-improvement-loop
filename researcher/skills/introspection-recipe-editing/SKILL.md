---
name: introspection-recipe-editing
description: Use when editing this project's Introspection agent recipe and checking its local manifest-facing structure.
---

# Introspection Recipe Editing

The active recipe is copied into `recipe/`. Keep edits inside that tree and preserve its existing conventions:

- `SYSTEM.md` contains the agent's top-level behavior and filesystem instructions.
- `agents/` contains agent configuration; `extensions/` contains registered TypeScript tools.
- `skills/<name>/SKILL.md` contains task-specific workflows, with helper scripts beside the skill.
- `python/pyproject.toml` and `python/uv.lock` describe recipe-local Python dependencies.
- `package.json` and its lockfile describe recipe-local JavaScript dependencies.

Inspect the existing files before editing. Keep stable sandbox paths consistent with the recipe, avoid promising tools or packages that are not configured, and do not edit the Introspection manifest, evaluator, runner, or deployment state from this workspace.
