# Self-Improvement Loop Implementation

This document captures the working design for the first self-improvement loop.
It complements `docs/self-improvement-run-model.md`, which explains recipe copy
and runtime identity choices.

## Goal

Build a script-orchestrated improvement loop that behaves like a small ML
training loop:

1. Establish a baseline score.
2. Run train epochs against a mutable working recipe.
3. Let a Codex researcher inspect train failures and edit the working recipe.
4. Re-run the train split and track score movement over epochs.
5. Optionally run the held-out test split for private transfer monitoring.
6. Preserve promising local maxima as checkpoint PR candidates.

The first version should not try to be a fully autonomous researcher agent. It
should create the right artifacts and boundaries so a future researcher agent
can drive the same loop.

## High-Level Flow

```text
bootstrap self-improvement run
  copy seed recipe into recipes/self-improvement/<run-id>/legal-agent
  create .introspection/<run-id>.yaml
  commit bootstrap state
  create one Introspection runtime for the run
  start/use introspection dev for live development

epoch 000
  run train split
  optionally run held-out test split privately
  record baseline metrics

epochs 001..N
  prepare researcher packet from train-only evidence
  invoke Codex researcher
  enforce edit allowlist
  invoke fresh verifier
  ask same researcher session to revise if verifier fails
  run train split with updated working recipe
  optionally run held-out test split privately
  update journal, experiment records, and metrics
  explicitly check whether this epoch advances the best candidate
  checkpoint/PR when that explicit check finds a new local maximum
```

## Researcher Visibility

The researcher should see train evidence and prior research memory, not held-out
test details.

Allowed researcher inputs:

- current train aggregate,
- last epoch train result,
- best train result so far,
- train conversations/traces,
- train generated artifacts,
- train Harvey evaluations,
- baseline recipe as read-only comparison material,
- current working recipe,
- prior research journal and experiment records.

Not included in researcher packets:

- held-out test conversations,
- held-out test generated artifacts,
- held-out test rubric/evaluation details,
- Harvey evaluator internals or criteria outside the train result artifacts,
- unrelated repository code.

For V1, held-out test scores can be tracked by the orchestrator/UI for transfer
monitoring, but they should not be fed into the researcher prompt.

## Research Memory

The loop should maintain explicit file-backed memory instead of relying only on
Codex session state.

Suggested structure:

```text
results/self-improvement/<run-id>/
  metadata/
    run.json
    best_candidate.json
    metrics.jsonl
    metrics.csv
  workspace/
    journal.md
    hypotheses.md
    experiments.md
    current_plan.md
  epochs/
    epoch-000/
      train_result_ref.json
      test_result_ref.json
      researcher_packet.md
      notes.md
    epoch-001/
      ...
```

`journal.md` is the narrative research log.

`hypotheses.jsonl` records hypotheses such as:

```json
{"id":"hyp-003","epoch":2,"text":"The agent is failing when it drafts before reading every attachment.","status":"active"}
```

`experiments.jsonl` records attempted changes, result summaries, and decisions:

```json
{
  "epoch": 3,
  "hypothesis_id": "hyp-003",
  "change_summary": "Added an explicit file-inventory step before drafting.",
  "train_task_pass_rate": 0.5,
  "train_rubric_pass_rate": 0.84,
  "decision": "keep"
}
```

`metadata/best_candidate.json` is maintained by an explicit best-candidate
check. It should not be rewritten as a side effect of rebuilding metrics.

`metadata/metrics.jsonl` and `metadata/metrics.csv` feed score-over-epoch graphs.

## Researcher Session Mode

Researcher invocation should support two epoch-level modes:

```yaml
researcher:
  epoch_session_mode: fresh  # fresh | resume
```

`fresh` starts a new Codex session each epoch and supplies memory through files.

Advantages:

- more reproducible,
- less context contamination,
- easier to avoid held-out leakage,
- forces good research artifacts,
- easier to debug later.

`resume` keeps using the same Codex session across epochs.

Advantages:

- less rediscovery,
- stronger continuity,
- useful during interactive/manual development.

Tradeoffs:

- hidden memory is harder to audit,
- stale assumptions can persist,
- accidental held-out details would be harder to remove,
- long sessions may drift or compact.

Default V1 mode should be `fresh`. Resume mode can be supported as an
interactive option.

The epoch-level mode applies across epochs only. Within one epoch, verifier
feedback should go back to the same researcher session that made the original
edit. This lets the researcher revise with full context on the change it just
made.

```text
researcher attempt
fresh verifier attempt
if verifier fails:
  same researcher epoch session revises
  fresh verifier attempt
```

## Structured Step Completion

Researcher and verifier steps should terminate through structured final output
captured by the orchestrator, not manually written sentinel files.

The orchestrator can invoke Codex with a JSON schema and capture the final
message:

```bash
codex exec \
  --output-schema researcher.schema.json \
  --output-last-message researcher_result.json \
  < researcher_prompt.md
```

The researcher result should include fields like:

```json
{
  "status": "done",
  "hypothesis": "...",
  "change_summary": "...",
  "files_changed": ["..."],
  "ready_for_verification": true
}
```

The verifier result should include fields like:

```json
{
  "verdict": "pass",
  "risk_level": "low",
  "issues": [],
  "summary": "No task-specific leakage detected.",
  "revision_instructions": null
}
```

The saved structured results become part of the epoch record. They are also the
orchestrator's completion signal for each step.

## Verification And Revision

An optional verifier should review researcher edits before the next train run.
The verifier is always a fresh Codex invocation; it does not need a session mode.

Verifier responsibilities:

- check whether edits are sample-specific,
- check whether task IDs, exact filenames, party names, document facts, or
  rubric language were hard-coded,
- check that the change is plausibly general across legal document tasks,
- check that only allowed paths were edited,
- summarize risks and provide revision instructions when needed.

Suggested config:

```yaml
verifier:
  enabled: true
  max_revision_attempts: 2
  block_on_fail: true
```

If verification fails, the orchestrator should pass the verifier result back to
the same researcher epoch session and ask it to revise. Then a new fresh
verifier invocation checks the revised state. If the verifier still fails after
`max_revision_attempts`, the epoch stops for human review.

## Researcher Edit Boundary

Codex should not run from the repository root with broad write access.

The orchestrator should invoke Codex from a constrained researcher workspace and
give it only the files it needs. The intended writable surface is:

```text
recipes/self-improvement/<run-id>/legal-agent/**
results/self-improvement/<run-id>/workspace/**
```

The baseline recipe may be provided read-only for comparison. Harvey LAB
evaluation code, held-out test artifacts, runner code, and unrelated repository
files should not be writable by the researcher.

The loop should also perform a post-run diff allowlist check. If Codex changed a
file outside the allowed paths, the epoch should fail and require human review
or automatic revert of disallowed edits.

The diff allowlist check is separate from verifier judgment. It is a mechanical
guardrail and should run even when the verifier is disabled.

## Checkpointing And PRs

The loop does not need to commit every epoch.

Instead, it should checkpoint promising recipe states. Best-candidate state is
an explicit orchestration decision after an epoch has completed and metrics have
been refreshed:

```text
refresh metrics
explicitly update best_candidate.json
if best_candidate.is_new_best and best_candidate.best_epoch > 0:
  create or prepare checkpoint/PR before the next researcher invocation
continue the next epoch from the current working recipe
```

Epoch 0 is the baseline and should not trigger a PR. A regression after a prior
best should not automatically roll back the working recipe in V1; the researcher
continues from the current working recipe and can decide whether to recover,
revert, or pursue a different hypothesis.

The PR should describe:

- seed recipe/run,
- epoch number,
- hypothesis/change summary,
- train score before and after,
- held-out test aggregate if available,
- task/rubric coverage,
- known failures or reliability issues.

This creates durable candidate states without turning every experiment into a
commit.

## Metrics To Track

Core V1 metrics:

- train task pass rate,
- train rubric pass rate,
- train task coverage,
- train scored trial coverage,
- held-out test task pass rate,
- held-out test rubric pass rate,
- held-out test task coverage,
- best-so-far epoch.

Future metrics:

- total agent wall-clock time per task,
- total eval time,
- model/tool token usage,
- estimated task cost,
- estimated eval cost,
- cost per passed task,
- score improvement per added cost,
- reliability rates by failure class.

The long-term objective should include quality and efficiency:

```text
improve pass rates without meaningfully increasing cost or runtime
```

Cost and time should eventually be available to the researcher as feedback, but
V1 can start with score and coverage graphs.

## Future Researcher Agent

The fully autonomous researcher agent can be built later on top of the same
interfaces:

- researcher packets,
- file-backed journal and experiment records,
- working recipe edit allowlist,
- epoch metrics,
- checkpoint rules,
- train/test separation.

The first implementation should therefore optimize for clear artifacts and
replayability, not maximum autonomy.

## Implementation Roadmap

### Phase 1: Base Orchestration Setup

Goal: create the self-improvement run skeleton and make train/test epoch runs
land in the right directories without invoking Codex yet.

Changes:

1. Add a self-improvement run config format.

   Example fields:

   ```yaml
   id: run-001
   seed_recipe: recipes/legal-agent
   working_recipe: recipes/self-improvement/run-001/legal-agent
   manifest: .introspection/self-improvement-run-001.yaml
   split_config: experiment_configs/baseline_split.yaml
   max_epochs: 10
   test_every: 3
   researcher:
     epoch_session_mode: fresh
   verifier:
     enabled: true
     max_revision_attempts: 2
     block_on_fail: true
   ```

2. Add a bootstrap command.

   Responsibilities:

   - copy the seed recipe to the working recipe path,
   - create the `.introspection/<run-id>.yaml` manifest,
   - create `results/self-improvement/<run-id>/`,
   - create `workspace/`, `epochs/`, `test/`, and `references/`,
   - copy or snapshot the seed recipe under `references/baseline_recipe/`,
   - initialize `workspace/journal.md`, `hypotheses.md`, `experiments.md`, and
     `current_plan.md`.

   This is a low-level setup command. The normal V1 entrypoint is `start`.

3. Add a start command.

   Responsibilities:

   - bootstrap a fresh run,
   - run epoch 0 train,
   - always run epoch 0 held-out test,
   - refresh metrics,
   - explicitly update `best_candidate.json`.

   V1 does not need first-class resume support. If `start` is interrupted or a
   run id already exists, use the lower-level commands intentionally rather than
   having the orchestrator infer partial state.

   Example:

   ```bash
   uv run python -m runner self-improve start \
     --config self-improvement-configs/baseline.yaml \
     --runtime-id <runtime-id>
   ```

   If `--run-id` is omitted, `start` generates a timestamp-based id. A provided
   `--run-id` is treated as an explicit override. In both cases, bootstrap
   checks for pre-existing recipe, manifest, and results paths before creating
   the run.

4. Extend the runner to write experiment output into a caller-supplied directory.

   The self-improvement layout should be:

   ```text
   results/self-improvement/<run-id>/
     epochs/
       epoch-000/
         aggregate.json
         <task-id-safe>/
           task_summary.json
           trial-001/
             conversation.json
             outputs/
             evaluation/
             incidents.json
             trial_summary.json
     test/
       epoch-000/
         aggregate.json
         <task-id-safe>/
           ...
   ```

   `epochs/` contains train evidence only. `test/` contains held-out evidence
   and should never be made available to the researcher.

5. Add a command to run an epoch's train split.

   Example:

   ```bash
   uv run python -m runner self-improve run-train \
     --config self-improvement-configs/baseline.yaml \
     --epoch 0 \
     --runtime-id <runtime-id>
   ```

6. Add a command to run an epoch's held-out test split.

   Example:

   ```bash
   uv run python -m runner self-improve run-test \
     --config self-improvement-configs/baseline.yaml \
     --epoch 0 \
     --runtime-id <runtime-id>
   ```

7. Add metrics aggregation for self-improvement runs.

   Generate:

   ```text
   results/self-improvement/<run-id>/metadata/metrics.jsonl
   results/self-improvement/<run-id>/metadata/metrics.csv
   ```

   Include train metrics for every epoch and test metrics only when available.

8. Add best-candidate tracking.

   Maintain:

   ```text
   results/self-improvement/<run-id>/metadata/best_candidate.json
   ```

   This should be an explicit command or orchestrator step, not a side effect of
   metrics refresh. It should track the best train score so far, epoch id,
   recipe path, runtime id, whether this check found a new best, and whether a
   checkpoint/PR has been created.

9. Add mechanical researcher edit validation.

   Before Codex automation, implement the allowlist check as a standalone
   utility/command:

   ```text
   allowed writes:
     recipes/self-improvement/<run-id>/legal-agent/**
     results/self-improvement/<run-id>/workspace/**
   ```

   This should reject edits to baseline recipe, Harvey LAB, runner code,
   experiment configs, `.introspection`, prior epoch results, and held-out test
   artifacts.

### Phase 2: Researcher And Verifier Invocation

Goal: invoke Codex against the prepared artifacts with strict boundaries, but
keep prompts and skills simple enough to iterate.

Changes:

1. Add researcher workspace preparation.

   The researcher should receive:

   - path to the working recipe,
   - read/write access to `workspace/`,
   - read-only access to `epochs/`,
   - read-only access to `references/`,
   - no access to `test/`.

2. Add researcher JSON schema.

   Capture structured final output as:

   ```text
   results/self-improvement/<run-id>/epochs/epoch-XXX/researcher_result.json
   ```

3. Add verifier JSON schema.

   Capture structured final output as:

   ```text
   results/self-improvement/<run-id>/epochs/epoch-XXX/verifier_result.json
   ```

4. Add researcher invocation command.

   It should support:

   ```yaml
   researcher:
     epoch_session_mode: fresh  # fresh | resume
   ```

   Within one epoch, verifier feedback should resume the same researcher
   session for revisions.

5. Add verifier invocation command.

   The verifier is always a fresh Codex invocation and has no session mode.

6. Add verifier revision loop.

   Flow:

   ```text
   researcher edits
   allowlist check
   fresh verifier checks
   if verifier fails:
     send verifier result to same researcher epoch session
     researcher revises
     allowlist check
     fresh verifier checks again
   stop after max_revision_attempts
   ```

7. Add checkpoint candidate detection.

   If train task pass rate reaches a new best, mark the epoch as a local
   maximum and prepare or create a checkpoint branch/commit/PR according to
   config.

### Phase 3: Prompts And Skills

Goal: improve the quality and safety of researcher/verifier behavior.

Changes:

1. Create a `self-improvement-research` Codex skill.

   It should describe:

   - hypothesis-driven research process,
   - train/test separation,
   - how to use `workspace/`, `epochs/`, and `references/`,
   - how to write journal/hypothesis/experiment notes,
   - how to avoid sample-specific overfitting,
   - how to make small general recipe changes.

2. Create or adapt an Introspection recipe-editing skill.

   It should cover:

   - recipe structure,
   - `SYSTEM.md`,
   - skills and scripts,
   - custom tools/extensions,
   - recipe-local dependencies,
   - sandbox paths such as `/workspace/files`, `/workspace/outputs`, and
     `/workspace/.pi/skills`.

3. Create a verifier skill or verifier prompt.

   It should focus on:

   - sample-specific leakage,
   - task ID / filename / party-name hard-coding,
   - rubric leakage,
   - allowed edit paths,
   - whether the change is plausibly general.

4. Iterate researcher prompts.

   The prompt should include:

   - `latest_completed_epoch`,
   - `next_epoch_to_prepare`,
   - current train score,
   - best train score so far,
   - previous researcher notes,
   - exact edit boundaries,
   - expected structured output.

5. Iterate verifier prompts.

   The prompt should include:

   - researcher result,
   - current diff,
   - changed files,
   - relevant train-only context,
   - expected structured output.

### Phase 4: Smoke Tests

Goal: prove each part with a small, cheap split before running the baseline
train/test loop.

Smoke sequence:

1. Bootstrap `run-001` from `recipes/legal-agent`.
2. Commit bootstrap state if creating a runtime identity.
3. Create or select the self-improvement runtime.
4. Start `introspection dev` for the run runtime.
5. Run `epoch-000` train on `smoke_split`.
6. Verify metrics and directory layout.
7. Run researcher in dry-run or constrained mode.
8. Verify allowlist enforcement.
9. Run verifier on a known safe edit.
10. Run verifier on a deliberately sample-specific edit.
11. Run `epoch-001` train after a small benign recipe edit.
12. Confirm metrics graph inputs update correctly.
