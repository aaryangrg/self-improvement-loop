---
name: introspection-recipe-editing
description: Use when editing this project's Introspection agent recipe and checking its local manifest-facing structure.
---

# Introspection Recipe Editing

The candidate recipe is staged under `recipe/`. Inspect its current files and preserve its conventions. Choose the owning layer for the behavior you intend to change:

| Need | Recipe location | Check before editing |
| --- | --- | --- |
| General agent behavior or workspace paths | `SYSTEM.md` | Does the instruction apply across tasks without naming a sample or rubric? |
| Agent composition or tool/skill selection | `agents/*.yaml` | Is the selected tool or skill actually registered by the recipe? |
| Reusable model-driven procedure | `skills/<name>/SKILL.md` and helpers | Is it a workflow requiring judgment rather than a deterministic operation? |
| Deterministic file operation or custom tool | `extensions/*.ts` or helper scripts | Is it registered in `package.json`, and exposed to the agent where required? |
| Python runtime dependency | `python/pyproject.toml`, `python/uv.lock`, and `package.json`'s `pi.runtime.python` | Is the version locked and available in the managed runtime? |
| JavaScript runtime dependency | `package.json` and `pnpm-lock.yaml` | Is the lockfile synchronized with the declared dependency? |

The staged agent YAML intentionally omits `ai` and `session`; do not add or alter them. The orchestrator restores these frozen settings when assembling the deployable recipe. Do not change model selection indirectly through another recipe field.

Registration and selection are distinct: `package.json` declares recipe resources, while agent configuration controls which tools and skills are available to that agent. Inspect both before assuming a new tool or skill is usable. For custom tools, also check that its implementation and description match what the agent will see.

Keep sandbox paths and skill instructions consistent with real capabilities. An import or skill mention alone does not make a package or system binary available. If a new dependency is necessary, update the declaration and lockfile only when you can verify that workflow locally; otherwise choose a change using existing capabilities. Do not claim validation you cannot run: the staged recipe is incomplete, and the orchestrator checks its assembled version.

Do not edit the Introspection manifest, evaluator, runner, deployment state, or research instructions from this workspace.
