# Self-Improvement Run Model

## Decision

Each self-improvement run gets its own copy of the seed recipe and its own
Introspection manifest. `recipes/legal-agent/` remains the reusable baseline;
the runner does not edit it during research.

```text
recipes/legal-agent/                                  baseline seed
recipes/self-improvement/<run-id>/legal-agent/        run's working recipe
.introspection/self-improvement-<run-id>.yaml         run's runtime manifest
results/self-improvement/<run-id>/                    local evidence and metadata
```

`seed_recipe` in the run config selects the source directory. A later run can
start from the baseline or from a chosen recipe produced by an earlier run.
Starting from a particular historical commit requires checking out or
materializing that recipe version as the seed; the config does not resolve Git
commits by itself.

## Runtime Sequence

1. Copy the seed recipe and write a manifest pointing at the copy.
2. Commit and push both on `main`, then create a distinct Introspection runtime
   for the run. Score train and held-out test on that baseline version.
3. Create the run branch. Push the first candidate commit and open a draft PR.
   Later revisions remain on the same PR branch.
4. For each candidate, wait for a runtime version under the PR ref whose recipe
   commit SHA matches the pushed commit. Validate and smoke-test that exact
   version before scoring it. Once approved, pin it for the next epoch.
5. Keep the best train-scoring candidate's epoch, commit, and PR reference in
   run metadata. The PR history retains rejected and regressive candidates;
   they are not committed directly to `main`.

The per-run recipe path and runtime identity make experiments independent.
Their end states can be compared or reused as seeds without overwriting the
baseline. The cost is extra recipe copies, manifests, and runtime identities;
shared baseline changes do not automatically flow into existing runs.

## Why PR-Backed Staging

We first investigated `introspection dev` for serving uncommitted local edits
to cloud tasks. In this project, `introspection dev` exited without a lasting
attachment, and development task creation returned HTTP 500. The same recipe
worked in staging. A missing CLI `remotes` capability or scope was a likely
cause, but we did not establish it conclusively.

The Git-backed route therefore provides a reproducible version for each
candidate. A local recipe check can run before pushing; the cloud build still
requires a pushed Git ref. If the development lane becomes available, it could
reduce per-epoch commit/build overhead, but the current runner uses PR-backed
versions. See [Architecture](architecture.md) for the full validation and
scoring loop.
