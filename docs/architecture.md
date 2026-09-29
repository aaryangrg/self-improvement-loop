# Architecture

The runner starts with a baseline evaluation, then cycles through a fixed number
of training epochs. In each epoch, Codex acts as an independent researcher: it
studies the previous training results, forms a hypothesis about an agent
failure, and edits the recipe to test an improvement. The runner executes the
updated agent and evaluates its outputs. In the next epoch, Codex sees whether
the change helped and decides whether to refine that approach or try a new
hypothesis. This loop runs without human intervention.

The system brings together:

- **Introspection CLI:** creates runtimes, starts agent tasks, and retrieves
  conversations and generated files.
- **Harvey LAB:** supplies legal tasks, input documents, and output evaluation.
- **Sandboxed Codex CLI:** runs the researcher and a separate adversarial
  verifier that reviews proposed recipe changes.
- **GitHub PRs:** provide Git-backed candidate versions for evolving staging
  runtimes.
- **Streamlit:** shows live progress, scores, and research decisions in a local
  observability dashboard.

## Models In The Smoke Run

The completed smoke run used four model roles. The run config selects the
judge, researcher, and verifier models; the seed recipe selects the task
agent's model.

| Role | Model | Where selected | Purpose |
| --- | --- | --- | --- |
| Task agent | `anthropic/claude-sonnet-4-6` | `recipes/legal-agent/agents/agent.yaml` | Execute Harvey tasks in Introspection's managed runtime. |
| Harvey judge | `gpt-6-sol`, medium reasoning | `self-improvement-configs/smoke.yaml#evaluation` | Score downloaded deliverables against Harvey criteria through the local evaluator. |
| Codex researcher | `gpt-6-sol`, xhigh reasoning | `self-improvement-configs/smoke.yaml#researcher` | Analyze train evidence and propose recipe edits. |
| Codex verifier | `gpt-6-sol`, medium reasoning | `self-improvement-configs/smoke.yaml#verifier` | Review each candidate for sample-specific changes. |

The researcher and verifier use the dedicated Codex login. The Harvey judge
uses the configured API key; the task agent uses Introspection's managed LLM.
The researcher cannot change the task agent's model through its editable
recipe copy.

## Run Flow

```text
+--------------------+ -> +--------------------+ -> +--------------------+
| 01 Configure       |    | 02 Bootstrap       |    | 03 Baseline        |
+--------------------+    +--------------------+    +--------------------+
                                                      |
                                                      +--> enter epoch loop

+--------------------------- repeat up to N epochs ---------------------------+
|                                                                             |
|    +----------------------+                 same-session revision           |
| +->| 04 Research          |<----------------------------------+             |
| |  | hypothesis + edit    |                                   |             |
| |  +----------------------+                                   |             |
| |             |                                               |             |
| |             v                                               |             |
| |  +----------------------+                                   |             |
| |  | 05 Validate          |-------------- failure ------------+             |
| |  | static/build/smoke   |                                   |             |
| |  +----------------------+                                   |             |
| |             |                                               |             |
| |             v                                               |             |
| |  +----------------------+                                   |             |
| |  | 06 Review            |------------- rejection -----------+             |
| |  | fresh Codex verifier |                                                 |
| |  +----------------------+                                                 |
| |             | approved                                                    |
| |             v                                                             |
| |  +----------------------+                                                 |
| |  | 07 Evaluate          |                                                 |
| |  | train + test if due  |                                                 |
| |  +----------------------+                                                 |
| |             |                                                             |
| |             v                                                             |
| |  +----------------------+                                                 |
| +--| 08 Metrics / decide  |  next epoch                                     |
|    | scores + dashboard   |                                                 |
|    +----------------------+                                                 |
+-----------------------------------------------------------------------------+
               | finish
               v
             Done
```

Epoch 0 always runs both splits. Later held-out tests run at the configured
cadence and on the final epoch. Test scores are visible to the operator and
dashboard, but test details are never supplied to the researcher.

## Stages 01-02: Configure And Bootstrap

**01 Configure**

- A file in `self-improvement-configs/` selects the seed recipe and split
  config, Harvey judge and reasoning settings, number of epochs, test cadence,
  and researcher/verifier models and revision limits.
- A file in `experiment_configs/` defines the train and held-out test task IDs,
  trials per task, and separate task/evaluation concurrency. Together, these
  two files define what the run evaluates and how it runs. Judge settings live
  in the self-improvement config, not the split config.

**02 Bootstrap**

- Copy the chosen seed into `recipes/self-improvement/<run-id>/legal-agent/`;
  leave the seed untouched. Write a fresh
  `.introspection/self-improvement-<run-id>.yaml` pointing at the copy.
- Commit and push the copied recipe and manifest on `main`, then create a
  distinct Introspection runtime from that commit. Run the train and test
  baseline against it.
- Create a run branch. On the first proposed edit, push the candidate and open
  a draft PR. For each later candidate, wait for the runtime version built
  from its exact PR commit; after validation and review, pin that version for
  the next epoch.

This gives each run independent recipe history: one can start from the baseline,
while another can seed from a chosen recipe checkpoint from a prior run. The
PR-backed staging path also lets the runner test successive versions without
replacing the baseline. We chose it after `introspection dev` repeatedly failed
with 500 errors in this project; a missing CLI `remotes` capability/scope was
the likely cause, not a confirmed diagnosis. See
[Run Model](self-improvement-run-model.md) for the investigation.

## Stages 03 And 07: Execute And Score

Train and held-out test use the same execution pipeline. They differ in which
Harvey task IDs are selected and who can inspect the results: the researcher
sees train evidence, but not test evidence.

**For each task and trial:**

1. Read Harvey's task instructions and input documents. Build a prompt without
   exposing the task ID or grading criteria.
2. Upload the documents, reusing cached Introspection file IDs after checking
   they are still available. Mount inputs under `/workspace/files`.
3. Start the task on the selected runtime version. When execution finishes,
   save the conversation and download deliverables from `/workspace/outputs`.
4. Evaluate the downloaded files with Harvey LAB and save its `scores.json`.

Evaluations start as soon as their tasks finish. Separate concurrency limits
control agent tasks and evaluations. Each scored trial contributes to a
per-task summary; the split score averages those task-level pass rates. The
aggregate also reports how many tasks received a score, so a score from an
incomplete split is recognizable.

**Failures remain visible.** The runner keeps any conversation, outputs, and
error details it can recover, but excludes unscored trials from pass rates. It
retries certain transient CLI, stream, and download failures. If fewer than
half the tasks have a score after the first pass, it runs one additional trial
for each task still without a score.

## Stage 04: Researcher

The researcher runs as a Codex CLI subprocess after each scored train epoch.
It edits a staged copy of the working recipe while the run's local results
provide its research history:

```text
results/self-improvement/<run-id>/
  epochs/                         completed train evidence (read)
  references/baseline_recipe/     original recipe for comparison (read)
  workspace/                      journal, hypotheses, experiments (read/write)
  test/                           held-out evidence (no access)
  metadata/researcher/epoch-NNN/
    sandbox/
      AGENTS.md                   researcher instructions (read)
      .agents/skills/             two research/recipe guides (read)
      instructions/               final-output schema (read)
      recipe/                     staged candidate recipe (read/write)
    codex-home/                    invocation-specific HOME
    researcher_result.json         hypothesis, changes, expected outcome
    events.jsonl                   Codex events for audit/debug
```

**Filesystem permissions.** Codex receives an explicit path allowlist: it can
read completed train epochs, the baseline recipe, and staged instructions; it
can write only the staged `recipe/` and the run's `workspace/` research notes.
The held-out `test/` directory, Harvey dataset, evaluator, runner, and other
repository files are outside that allowlist. Network access is disabled.
Only the candidate recipe is copied into the sandbox; train evidence and notes
remain in their original run directories. After each turn, the runner compares
file snapshots and rejects changes to read-only sandbox files. The orchestrator,
not Codex, later promotes an accepted recipe into the working run directory.

**Protected model and session settings.** Before Codex sees the candidate,
the runner removes `ai` and `session` from its agent YAML. During validation,
it restores those fields from the trusted working recipe and rejects a changed
`package.json` agent selector. The researcher can therefore change instructions,
skills, tools, and compatible recipe dependencies, but cannot deploy a different
model or session configuration through its staged recipe.

**Instructions and skills.** The staged `AGENTS.md` comes from
`researcher/AGENTS.md` and defines the role, research method, workspace layout,
and edit boundaries. Two staged skills add focused guidance:
`self-improvement-research` covers failure analysis and testable hypotheses;
`introspection-recipe-editing` covers recipe structure and editing contracts.
The per-epoch prompt names the run and latest train scores, points to earlier
epochs and research notes, and asks for a candidate edit. A JSON schema requires
a final report with the hypothesis, changes made, and expected outcome. Each
epoch starts a fresh Codex session, but prior results and notes remain available;
gate failures and verifier rejections resume that epoch's same session.

**Isolated Codex login.** Researcher and verifier processes share a persistent
project-specific `CODEX_HOME` at `~/.self-improvement-loop/codex`, separate from
the developer's normal Codex configuration and skills. Each researcher epoch
also receives its own `HOME` under `metadata/researcher/epoch-NNN/codex-home/`.
The CLI is launched with user configuration and rules ignored, and its shell
environment is restricted to basic process variables. This avoids importing
personal instructions and credentials into the research process. On the first
`self-improve start`, the runner checks the dedicated Codex login and starts an
interactive sign-in if needed. That normally means signing in once for this
dedicated profile, not once per epoch; expired credentials may require another
sign-in.

## Stage 05: Validate Candidate

The runner gates each proposed edit before spending a full train epoch on it:

1. **Check the edit boundary.** Compare the staged files and research notes
   with their pre-turn snapshots. Changes to read-only sandbox files are
   rejected. If the researcher changed only its notes, the runner asks the
   same Codex session to make a concrete recipe edit.
2. **Check the recipe locally.** Copy the staged candidate into a temporary
   directory, restore the trusted `ai` and `session` fields, and point a
   disposable copy of the run manifest at it. Run `introspection check` there
   without changing the real manifest. This catches recipe validation errors
   before a Git push; diagnostics go back to the same researcher session.
3. **Build the exact candidate.** Provisionally copy the checked recipe into
   the run's working recipe, commit and push only that recipe and its manifest
   on the run branch, and open the draft PR if this is its first candidate.
   Poll Introspection for a version under that PR ref whose recipe commit SHA
   matches the pushed commit. Continue only when its image is ready and recipe
   validation is valid; build failures and timeouts go back to the researcher.
4. **Prove startup.** Start one unscored task on that exact runtime version
   with no input files. The task asks for a short reply, then the runner checks
   that the root agent was invoked and did not report an execution error. A
   startup failure returns its diagnostic for another revision. This smoke
   task does not consume a Harvey sample or contribute to scores.

A local recipe check cannot prove the cloud image will build or that the agent
will start, so both checks are necessary. The cloud build requires a pushed Git
ref, which means a candidate is committed *before* adversarial review. A
rejected candidate remains in the draft PR's history; each repair is another
commit and another exact-version check.

**Correction loop.** For a missing recipe edit, failed local check, build
failure, startup failure, or verifier rejection, the runner resumes the same
researcher Codex thread with a new prompt containing the diagnostic or review
feedback. The researcher can inspect its prior reasoning, revise the staged
recipe, and submit another structured result. The gate sequence then restarts
with the revised candidate. The limits are independent: `max_code_revisions`
counts no-edit/check/build/smoke corrections, while
`max_revision_attempts` counts verifier rejections. The baseline config allows
five code corrections and two verifier revisions per epoch. A write to a
read-only sandbox file is a boundary violation and stops the run rather than
entering this correction loop. After validation and review pass, the runner
pins the approved version for the next scored epoch.

## Stage 06: Adversarial Review

Only a candidate that passed the local recipe check, exact-commit cloud build,
and startup smoke reaches the verifier. Each review starts a fresh, ephemeral
Codex session. Its filesystem access is read-only: it can inspect the staged
candidate, working and baseline recipes, completed train epochs, research
notes, and a generated `candidate.diff`. It cannot edit the recipe, invoke a
deployment, or access held-out test results.

The diff compares the candidate with the recipe as it stood before this
epoch's first edit, so it still shows the full proposed change after a repair.
The invocation also includes the researcher's hypothesis and change summary.
`verifier/AGENTS.md` tells the verifier to check those claims against the
training conversations, generated outputs, evaluator scores, and successful
counterexamples, then ask whether the change would make sense on unseen tasks
of the same kind.

**What it rejects.** Hard-coded answers, task IDs, document facts, party names,
sample-specific filenames, rubric wording, or disguised lookup rules are
grounds for rejection. A general procedure is not disqualified just because
a training failure motivated it. Uncertain effectiveness alone is not grounds
for rejection: the next scored epoch tests that. This is a generalization and
evidence-integrity review, not another Harvey score.

**Handoff.** The verifier returns a structured `approve` or `reject` verdict
with a summary, issues, and revision instructions. An approval lets the runner
pin the checked runtime version and start the next scored epoch. A rejection
must identify a concrete issue; its feedback goes to the *same researcher
session* for an edit, followed by the full validation and review sequence
again. The verifier itself is fresh on every attempt, and its rejection limit
is separate from the code-correction limit described above.

## Stage 08: Metrics And Recovery

**Scores and candidate selection.** At the end of each epoch, the runner
records train task and rubric pass rates, test rates when scheduled, and counts
of evaluated tasks and scored/failed trials. `metadata/metrics.jsonl` and
`metrics.csv` provide one row per epoch for the dashboard curves. The best
candidate is selected by *train task pass rate*, with train rubric pass rate
as the tie-breaker; held-out test scores show whether improvements transfer,
but never select a candidate or enter the researcher's evidence. Because
unscored trials are excluded from pass rates, the coverage counts must be read
alongside each score. `metadata/best_candidate.json` records the selected
epoch and, for an improved recipe, its commit and draft PR reference.

**Live view and research history.** The local Streamlit dashboard reads run
progress and trial statuses while work is in flight, then plots the train/test
scores as epochs finish. It also shows researcher and verifier decisions,
Introspection-reported generation cost, estimated Harvey judge cost, and Codex
token usage. The researcher's `workspace/journal.md`, `hypotheses.md`, and
`experiments.md` explain why a change was tried; structured role outputs record
the decision at each handoff. Git commits identify the recipe versions that
were actually built and tested.

**Recovery boundary.** `self-improve resume` checks the saved run configuration,
switches to its recorded branch, and skips splits already marked finished.
Before replaying a partial split, it moves the old result directory under
`metadata/replays/` so its evidence is not lost. It can reuse a completed
researcher edit if validation had not begun. If an interrupted candidate gate
may already have committed, pushed, or deployed a version, the runner stops
for inspection rather than repeating a side effect whose outcome is uncertain.

## Monitoring Dashboard

`self-improve start` prints a localhost Streamlit URL. The dashboard is
read-only and refreshes during a run; the terminal command remains in control
of execution. Select any saved local run from the sidebar. The status line and
epoch activity table show queued, executing, evaluating, scored, and failed
trials. Train and held-out test curves track task and rubric pass rates, with
coverage and failure counts available on chart hover.

Below the curves, cost and usage separate Introspection-reported generation
cost from the estimated Harvey judge cost and Codex token usage. Research
history groups each epoch's hypothesis, changes, expected outcome, verifier
decision, and exact candidate commit. The [README screenshots](../README.md#monitor-a-run)
show a completed two-epoch smoke run; the same page updates while an epoch is
in flight.

## Baseline Agent Construction

`recipes/legal-agent/` is a Harvey-inspired starting point, not a copy of
Harvey's full sandbox. We kept its document-task conventions where they fit
Introspection and mapped the rest to recipe-owned tools and dependencies.

**Prompt and tool mapping.** `SYSTEM.md` stays close to Harvey's baseline
prompt, but names the paths the agent actually sees: `/workspace/files` for
read-only inputs, `/workspace/outputs` for deliverables, and
`/workspace/.pi/skills/<name>/scripts/` for skill helpers. It does not tell
the agent to inspect Harvey's `task.json`, which is not supplied to the task
workspace. Harvey's `read` accepts Office and PDF files, while Introspection's
host `read` cannot be replaced by a recipe extension. Our extension therefore
registers `read_file` using Pi's standard read-tool definition. It preserves
ordinary text/image reads and routes DOCX, XLSX, PPTX, and PDF through a
recipe-local Python text/table parser. Pi's native `write` and `edit` remain
unchanged; the parser does not render documents or perform OCR.

**Skill adaptation.** We brought over Harvey's DOCX, XLSX, and PPTX authoring
guides, then changed their descriptions, examples, and helper paths to match
the tools and filesystem above. Reading an existing document goes through
`read_file`; a file-type skill guides *creating or editing* the deliverable.
For supported authoring paths, the guides point to recipe-local scripts:
Markdown-to-DOCX uses Python, spreadsheets use `openpyxl`, and slides can be
built with `python-pptx`, `md2ppt`, or PptxGenJS. We removed or replaced
instructions that assumed Harvey-only commands, rather than directing the
agent to call tools that may not exist in its cloud sandbox.

**Dependency mapping and limits.** The recipe includes Harvey's core Python
document packages for Office creation/editing, tables, and text-based PDF
extraction. `python/pyproject.toml` declares them, `python/uv.lock` pins their
resolved versions, and `package.json#pi.runtime.python` tells Introspection to
install and preflight that project. PptxGenJS is a recipe-local Node dependency.
We do not install Harvey's global JavaScript `docx` package; `python-docx`
covers our DOCX authoring path. We also omit `markitdown`: its transitive
`magika`/`onnxruntime` dependency failed in the managed runtime, so
`read_file` uses format-specific parsers instead.

Some workflows have partial replacements, not full parity. Direct Python
generation covers Markdown-to-DOCX and Markdown-to-PPTX without Pandoc or
Marp CLI. `openpyxl` can write formulas and request recalculation when a
workbook is opened, but it does not replace LibreOffice's calculation or
rendering. Python can extract text from digital PDFs, but Poppler-backed
rendering and Tesseract OCR for scanned pages are not available as declared
recipe capabilities. See
[Recipe Runtime Dependencies](recipe-runtime-dependencies.md) for the exact
Harvey-versus-recipe package and system-tool comparison.

## Future Scope

The two-epoch smoke run proves the end-to-end loop, not that the current
research strategy will scale or generalize. The next experiments should focus
on the quality and cost of research feedback:

- **Summarize failures before handing them to Codex.** Larger splits produce
  more evaluation calls, traces, and files than a researcher can usefully read
  each epoch. Keep every scored trial for audit, but build a deterministic
  index of failures by task, artifact type, execution phase, tool error, and
  rubric outcome. Let the researcher open representative traces and
  counterexamples from each group. This reduces repeated reading without
  discarding the underlying evidence.
- **Reduce the default evidence surface.** Give the researcher a compact
  per-epoch index and the files relevant to its current hypothesis, rather
  than presenting every conversation and deliverable as equally important.
  It should still be able to drill into train evidence when a summary is
  insufficient. This is an attention and context-window improvement, not a
  reason to delete raw results.
- **Grow and structure the dataset.** A one-task smoke split cannot establish
  generalization. Add more train and held-out tasks with overlapping *failure
  modes* but distinct documents and answers, so an improvement has a fair
  chance to transfer without being rewarded for memorizing a sample. Track
  coverage and variance across multiple trials as the split grows.
- **Experiment with validation feedback.** Detailed held-out test results
  should stay outside the researcher's reach; repeatedly feeding them back
  would turn that test into training feedback. If the researcher needs an
  intermediate signal, add a separate validation split it may inspect, and
  reserve a final test split for private transfer measurement. Even aggregate
  test scores revealed every epoch can steer the search, so that choice should
  be made explicitly.
- **Optimize quality alongside cost and time.** Feed task-agent generation
  cost and execution time back to the researcher, including slow-tail or
  worst-case runs, so it can seek higher pass rates without unbounded expense
  or latency. Keep judge and Codex research costs separate from task-agent
  costs when comparing candidate recipes.
- **Allow targeted train probes.** Give the researcher a bounded tool to run a
  chosen train task or small train subset before paying for another full epoch.
  Log every probe and its cost to avoid quietly overfitting through repeated
  sample selection. Cloud probes of a *changed* recipe still need a Git-backed
  runtime version today; truly uncommitted quick tests would require a working
  development lane or a local execution path with comparable behavior.

The longer-term direction is an independent researcher agent that coordinates
hypotheses, targeted probes, and full epochs within explicit time and spending
budgets. The orchestrator would still enforce recipe permissions, validation,
held-out separation, and human control over final promotion.
