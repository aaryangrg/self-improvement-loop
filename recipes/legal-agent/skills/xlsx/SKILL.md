---
name: xlsx
description: "Use this skill to author, edit, scan, or validate Microsoft Excel .xlsx files using the available Python spreadsheet stack. Supports writing formulas and marking workbooks for recalculation on open. For READING existing .xlsx files, use the `read` tool; this skill is for producing or modifying .xlsx deliverables. Triggers: 'build a model', 'create a spreadsheet', 'fill the schedule', 'edit a workbook'. Does NOT apply to .pdf, .docx, .pptx, or legacy .xls files."
---

# XLSX Authoring And Editing

> **Reading is not in scope.** To read an existing .xlsx, use the `read` tool.
> It extracts each sheet as text. Use this skill for writing, editing, scanning,
> and validating .xlsx files.

## Quick Reference

| Goal | Use |
|---|---|
| Build a workbook from scratch | `openpyxl` directly or `/workspace/skills/xlsx/scripts/build_workbook.py` |
| Write formulas | Assign formula strings such as `=B2*C2` with `openpyxl` |
| Edit cells in an existing file | `openpyxl.load_workbook(...)` -> mutate -> save |
| Scan for formula-error literals | `/workspace/skills/xlsx/scripts/scan_errors.py` |
| Validate before delivery | `/workspace/skills/xlsx/scripts/validate.py` |

Skill scripts are available at `/workspace/skills/xlsx/scripts/`. Invoke them
from `bash` with `python`.

## Formula Support

You can write formulas into cells. `openpyxl` preserves formula strings but does
not calculate Excel's cached formula results. For generated workbooks, set:

```python
wb.calculation.calcMode = "auto"
wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
```

This tells spreadsheet applications to recalculate formulas when the workbook is
opened. `/workspace/skills/xlsx/scripts/build_workbook.py` already sets these flags.

Use formulas for transparency:

- `=B2*C2` rather than a hard-coded computed result.
- Named ranges for cross-sheet assumptions where helpful.
- Adjacent unit labels so values are self-explanatory.

Avoid volatile formulas such as `OFFSET`, `INDIRECT`, `NOW`, and `TODAY` unless
the task specifically requires them.

## Banker Conventions

Apply these to every workbook unless the task explicitly overrides them:

- Inputs are blue, formulas are black, cross-sheet references are green, and
  external links are red.
- Negatives use parentheses, not minus signs.
- Red negatives in P&L tables.
- Accounting format for currency.
- Multiples are formatted as `0.0"x"`.
- Totals use underline borders instead of bold-and-underline.
- Avoid merged cells in input ranges.
- Put units in adjacent cells, not inside numeric-value cells.

`/workspace/skills/xlsx/scripts/build_workbook.py` applies these conventions from a JSON spec:

```bash
python /workspace/skills/xlsx/scripts/build_workbook.py spec.json /workspace/outputs/output.xlsx
```

## Error Scan

After creating or editing a workbook, scan for visible formula-error literals:

```bash
python /workspace/skills/xlsx/scripts/scan_errors.py /workspace/outputs/output.xlsx > /workspace/outputs/errors.json
```

The scan reports cells with `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#NULL!`,
`#NUM!`, or `#N/A`. Fix errors before delivering.

## Validation Gate

Always run validation before declaring the task complete:

```bash
python /workspace/skills/xlsx/scripts/validate.py /workspace/outputs/output.xlsx
```

Validation checks ZIP integrity, XML well-formedness, content types, sheet
relationships, and package structure.

## Out Of Scope

- Reading: use the `read` tool.
- Legacy `.xls` files.
- In-sandbox full Excel-compatible formula recalculation.
- PivotTable creation or modification.
- DAX measures and Power Pivot.
- VBA macros (`.xlsm`).
- Complex conditional formatting beyond simple cell-value rules.
- Complex charts beyond what `openpyxl` can reliably create or round-trip.
