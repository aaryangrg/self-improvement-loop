---
name: self-improvement-research
description: Use when analyzing completed training epochs and proposing general improvements to the working agent recipe.
---

# Self-Improvement Research

## Review The Previous Experiment

Read the latest `aggregate.json`, task summaries, and research notes first. If a prior hypothesis exists, compare its *predicted agent behavior* with the new traces and outputs, not only with the aggregate score. Separate whether the hypothesis was plausible, whether the recipe actually induced the intended behavior, and whether that behavior improved results. Continue, refine, or abandon the direction based on this evidence.

## Diagnose Failure Mechanisms

Move from aggregate results to affected tasks, then trials, `scores.json`, `conversation.json`, and generated outputs. For each promising failure group, trace the chain from task condition to agent decision, divergence, artifact, and evaluator consequence. A criterion verdict identifies an outcome; it does not by itself establish the cause. Cite concrete task IDs and trace points in the research notes, and check successful trials or counterexamples before generalizing. Do not turn rubric wording or one task's answer into a recipe instruction.

## Choose The Next Experiment

Identify the behavior you want to change and choose one general causal intervention. It may require coordinated edits across multiple recipe files. Before editing, record a prediction about observable agent behavior and what evidence in the next epoch would falsify it. Keep unrelated changes out so that the result remains interpretable.

Keep the run-results `workspace/journal.md` concise: prior prediction and observed outcome, evidence and counterexamples, next hypothesis, intended recipe change, predicted behavior, and falsifier. Add links or paths to relevant task results. Report the candidate using the required structured result; the orchestrator handles validation and scored execution.
