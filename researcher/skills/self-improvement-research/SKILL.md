---
name: self-improvement-research
description: Use when analyzing completed training epochs and proposing general improvements to the working agent recipe.
---

# Self-Improvement Research

1. Read the latest aggregate and task-level results at the train path given in the prompt, then inspect relevant traces and `scores.json` files for recurring failure patterns.
2. Compare the staged `recipe/` with the baseline recipe and prior research notes at the paths given in the prompt.
3. Write one falsifiable hypothesis before making a focused change. Prefer changes that address a repeated failure mechanism and avoid sample-specific instructions.
4. Record the observation, hypothesis, recipe edits, expected effect, and next experiment in the journal and experiment notes.
5. Return the required structured report. The orchestrator, not this researcher, runs the next experiment and evaluates changes.

Never seek held-out results, alter evaluation logic, or change files outside staged `recipe/` and the research notes directory.
