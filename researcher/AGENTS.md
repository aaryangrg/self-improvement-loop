# Researcher Instructions

You are improving one agent recipe using completed training-split evidence.

- Treat the baseline recipe as read-only context. Edit only the staged `recipe/` and the research notes directory named in the prompt.
- Use completed training epochs at the path named in the prompt. Train evaluation reports, including `scores.json`, are available for diagnosis. Held-out test material is unavailable.
- Prefer a general improvement supported by multiple observations over a task-specific patch.
- Keep changes focused, explain the hypothesis, and record decisions in the research notes directory's `journal.md` and relevant plan or experiment notes.
- Do not change evaluation criteria, the runner, task data, or these instructions. Do not commit or push.
- Finish with the JSON object required by `instructions/researcher-result.schema.json`.
