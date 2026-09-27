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
6. Use `introspection dev --runtime <runtime-id> --as <run-id>` for fast
   iteration against cloud development without committing every epoch.
7. Track epochs through the run ledger, patches, experiment results, and final
   candidate state.

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

After the runtime exists, `introspection dev` can serve saved local edits from
the working recipe to cloud development tasks. That lets an improvement loop run
many epochs without committing each epoch.

Commits should therefore be used for:

- bootstrapping a new self-improvement run,
- promising checkpoints,
- final candidates,
- PRs that promote a run's final state.

They are not required for every experimental epoch.

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
  "dev_target": "self-improvement-run-001",
  "dirty": true,
  "patch_file": "epochs/epoch-003/patch.diff",
  "experiment_run_id": "...",
  "hypothesis": "...",
  "decision": "keep"
}
```

This gives the researcher and future UI a readable history of hypotheses,
patches, scores, and decisions without duplicating the recipe for every epoch.
