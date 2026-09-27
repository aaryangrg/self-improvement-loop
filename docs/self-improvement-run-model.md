# Self-Improvement Run Model

This project treats `recipes/legal-agent` as the baseline seed recipe. A
self-improvement run should not edit that baseline directly. Instead, each run
gets its own working recipe copy and Introspection manifest.

## Structure

```text
recipes/
  legal-agent/
  self-improvement/
    run-001/
      legal-agent/

.introspection/
  legal-agent.yaml
  self-improvement-run-001.yaml
```

The run manifest points at the working copy:

```yaml
name: self-improvement-run-001
path: recipes/self-improvement/run-001/legal-agent
runtime:
  llm_mode: managed
```

## Lifecycle

1. Choose a seed recipe: the baseline, a previous run's final recipe, or a
   specific committed checkpoint.
2. Copy that seed into `recipes/self-improvement/<run-id>/legal-agent`.
3. Add `.introspection/<run-id>.yaml` for the working recipe.
4. Commit the new run copy and manifest so Introspection can create the runtime
   identity from a clean Git state.
5. Create the runtime once with `introspection runtimes create --manifest ...`.
6. Open a pull request for epoch edits and let Introspection build staging
   candidate versions from the PR ref.
7. Evaluate candidate versions through staging or exact runtime version ids.
8. Track epochs through the run ledger, result artifacts, candidate refs, and final
   candidate state.

`introspection dev` would be the ideal fast local-edit loop if available, but
it is not part of the current working plan because we could not get the cloud
development attachment to start reliably. See [Development Lane Finding](#development-lane-finding).

## Rationale

This model deliberately copies per self-improvement run, not per epoch.

The benefits are:

- The baseline recipe remains stable and reusable.
- Independent self-improvement runs can evolve without clobbering one another.
- A run can start from the baseline or from any previous run's end state.
- Each run has an unambiguous recipe path, manifest, runtime identity, ledger,
  and result history.
- The final state of a run can be proposed or promoted without silently
  mutating the baseline.

The tradeoffs are:

- The repository accumulates copied recipe directories and manifests.
- Shared baseline fixes do not automatically propagate into older run copies.
- Runtime identities may need eventual cleanup or archival.
- Git diffs across copied directories are noisier than branch-only evolution.

These tradeoffs are acceptable for the first architecture because run isolation
and lineage are more important than minimizing repository noise.

## Commit Boundary

Introspection runtime creation pins a Git-backed recipe state, so creating a new
runtime identity requires a clean committed run copy and manifest.

The first runtime for a new self-improvement manifest should be bootstrapped
from `main`. After that runtime group exists, Introspection can follow staged
candidate refs such as `pr/N`. That makes the practical iteration boundary:

- commit the initial run recipe and manifest to `main` so the runtime group can
  be created,
- create a PR branch for epoch edits,
- commit each candidate epoch state on that PR branch,
- pin or select staging candidate versions built from `pr/N`,
- promote or merge only useful candidates.

This is noisier than a live dev loop, but it avoids committing failed or
regressive epochs directly to `main`.

## Development Lane Finding

We investigated `introspection dev` as a way to serve uncommitted local recipe
edits into cloud development tasks. In our environment it did not work reliably:

- `introspection dev --runtime legal-agent --as self-improvement-dev --logs debug`
  exited successfully with no output and did not stay attached.
- The same behavior happened with runtime-name and runtime-id forms, from the
  repo root and from the recipe directory.
- Creating a development task against a ready runtime id failed with a 500
  response from the runtime run endpoint.
- The same runtime worked in staging and returned `RUN_FINISHED`.

So the current assumption is that the runtime image and recipe are healthy, but
the development attachment path is unavailable or not enabled for this project.
If Introspection support resolves this, we can replace the PR-backed epoch loop
with a faster dev-lane loop.

## Epoch Tracking

Epochs should be tracked in results and ledger artifacts, not by copying the
entire recipe directory again.

Each epoch should record enough provenance to understand what was tried:

```json
{
  "epoch": 3,
  "seed": "self-improvement-run-001",
  "recipe_path": "recipes/self-improvement/run-001/legal-agent",
  "runtime_id": "...",
  "candidate_ref": "pr/12",
  "candidate_commit": "...",
  "experiment_run_id": "...",
  "hypothesis": "...",
  "decision": "keep"
}
```

This gives the researcher and future UI a readable history of hypotheses,
patches, scores, and decisions without duplicating the recipe for every epoch.
