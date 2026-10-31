# Flow templates: clone + compose + launch-time ad-hoc steps — design

Date: 2026-10-31. Approach: **A — flatten + lineage pointer** (approved).
Status: brainstorming approved §§1–5; spec review gate pending.

Supersedes nothing; extends `2026-10-20-flows-design.md` §§3–7.

## 1. Intent

Three enhancements to flow templates, scoped by three user decisions:

1. **Clone** — duplicate a template as an editable baseline; the original
   is untouched.
2. **Compose** — combine N templates into one flat chain. Decision:
   **snapshot copy with lineage** (no live references; later edits to
   parents do not propagate).
3. **Ad-hoc steps** — add/remove/reorder steps before execution. Decision:
   **launch-time only** (working copy in the launch wizard; no mid-run
   chain surgery).

Versioning decision: **lineage pointer only** — `parent_labels` audit text
on the new row, no version numbers, runs do not pin versions. Full
versioning (approach C) is explicitly deferred; live sub-flow references
(approach B) are explicitly rejected.

## 2. Schema

- `flow_templates.parent_labels TEXT NOT NULL DEFAULT '[]'` — JSON array
  of source labels in order. Clone writes `["src"]`; compose writes
  `["a","b",...]`; hand-built templates stay `[]`. Audit text, not a
  join: no FK (labels rename via `--new-label`). Surfaced in list/detail;
  round-tripped by backup export/import (old files default `[]`; no
  `BACKUP_VERSION` bump unless the validator demands it).
- `flows.effective_steps TEXT` (nullable) — the launch-time chain
  actually executed. NULL = template steps (zero migration pain; all
  pre-change rows read as today). Non-NULL wins for resume cursor,
  `--skip-steps` bounds, `flow show`, and Flows detail. `step_results` /
  `current_step` index into the effective chain, so no cursor rework.

Migration: `ALTER TABLE` + `_schema_current` probes for both columns;
`store_flows._row/_flow_row` parse with `[]`/NULL defaults for
pre-migration rows.

## 3. Validation (one gate, one extension)

`validate_template` stays the single backend gate; frontend
`validateChain` mirrors it. New logic only:

- **Compose index mapping**: merged index → `source:local-index` so a
  violation names the contributing template and its local step.
- **Keep-first-start collapse**: before validation, drop every `start`
  except the merged chain's first step and surface a note (naive
  concatenation of two full templates would otherwise always fail the
  at-most-one-start rule). Any `start` not at merged position 0 after
  collapse is still rejected.
- **Effective chain**: `--steps` input validates through the exact same
  path as template save — no second rule set. The banned-flag scan
  (`then-fix-comments`, cleanup hard-delete/no-squash) runs on the final
  effective chain before any flow row exists (same fail-fast as today).

## 4. CLI

- `flow template clone <src> [--name] [--label]` → `create_template`
  copy of steps/pre/post with `parent_labels=["src"]`. Label defaults to
  the normalized new name (same rule as `add`).
- `flow template compose <label> --from a,b,c [--name]` → concatenated
  steps in listed order, single `validate_template` pass, index-mapped
  errors, `parent_labels=["a","b","c"]`.
- `flow run <tpl> <ref> --steps <JSON>` — full effective chain for this
  run only. Composes with `--step-config`: `--steps` replaces the base,
  `--step-config` deltas apply on top via the existing merge. Banned-flag
  scan on the final chain, fail fast.
- `flow show --json` gains `effective_steps` + `parent_labels`
  passthrough. `--dry-run` prints the resolved effective chain.

## 5. Web UI

- **FlowTemplates page**: per-row Clone button (prefills the add form,
  name suffixed ` (copy)`, label auto-suffixed, lineage noted); compose
  via row multi-select + "Compose selected" (concatenates into the same
  form as an ordinary editable chain); lineage rendered as a muted
  `from a, b` caption.
- **ChainEditor**: edit mode unchanged. Launch mode (FlowWizard) gains a
  working-copy state initialized from template steps with
  add/remove/drag-reorder; `validateChain` live, Run disabled until
  clean; `startRepoState` checks the working copy's first step. Confirm
  sends `--steps` + `--step-config`.
- **Flows detail**: effective steps when present (badge "modified at
  launch"), else template steps.

## 6. Parity checklist (same PR)

CLI flags → `web_args.py` SPECS/BOOL_FLAGS/VAL_FLAGS
(`flow template clone/compose`, `flow run --steps`) → `API_CONTRACT.md` +
`FEATURE_INVENTORY.md` → frontend controls → `tests/test_cli.py` +
`tests/test_webapp.py` (`test_specs_mirror_cli_flags` green).

## 7. Testing

Clone/compose store round-trip + lineage; multi-start collapse + note;
index-mapped compose errors; banned-flag scan on effective chain;
resume + `--skip-steps` reading `effective_steps`; `flow show` fallback
NULL → template steps; `validateChain` ↔ `validate_template` matrix;
`tsc` + `npm run build`; CLI smokes on scratch templates.

## 8. Explicit non-goals

Live template references, version numbers, run-pinned versions, mid-run
chain editing, parent-propagation of edits. Each was proposed and
rejected in brainstorming; do not reintroduce without a new decision.
