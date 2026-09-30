# SOP Standardization & Metadata Schema Conventions

This document describes the SOP-governance model and metadata-schema
conventions implemented in `sql/schema.sql`, `schemas/*.json`, and enforced
by `gxp_governance.py`. It is written the way a lab-wide data-governance
guide would be written for scientists, QA, and IT/LIMS integrators sharing
one relational data model.

## 1. Why SOP standardization matters here

Before this model, a typical failure mode across CLM and analytical teams
is that "which SOP governed this experiment" lives only in a lab notebook
entry or a filename convention, not in queryable data. That makes it
impossible to answer, at scale, questions like:

- "Every titer result run under SOP-ANA-001 v1.0 before the assay was
  updated — could any of these be affected by the method change?"
- "Was this sample drawn under an SOP version that was still effective on
  that date, or had it already been retired?"

The fix is architectural, not procedural: **SOP identity and SOP version
are first-class, foreign-keyed entities**, not text fields.

## 2. The SOP / SOP-version model

```
sop (1) ----< sop_version (many)
```

- `sop` is the stable identity of a procedure (`SOP-CLM-001`, "Cell Line
  Generation and Clone Selection"). It never changes once created.
- `sop_version` is the versioned, effective-dated instance of that
  procedure that activities actually link against. Each version has:
  - `version_label` (`v1.0`, `v2.0`, ...)
  - `effective_date` / `retired_date` — the window during which it
    governed real lab work
  - `approved_by` — the user (must hold an approval-capable role) who
    signed it off
  - `status` — `DRAFT`, `EFFECTIVE`, or `RETIRED`

Every governed activity table (`culture_vessel`, `sample`, `experiment`,
`instrument_run`) carries a `sop_version_id` foreign key — never a raw
`sop_id` and never a free-text version string. This means:

1. A DRAFT SOP version can exist in the system (e.g. while QA is reviewing
   a revision) without being usable to govern real work, because the
   linkage check (`gxp_governance.check_sop_version_linkage`) rejects any
   activity pointing at a DRAFT version.
2. An activity performed outside its linked SOP version's effective window
   (before `effective_date`, or after `retired_date`) is flagged — this
   catches the common real-world error of forgetting to advance an
   experiment's SOP reference after a procedure update goes live.
3. Domain mismatches are caught: an analytical experiment referencing a
   CLM-only SOP (or vice versa) is flagged, because SOPs are also tagged
   with a `domain` (`CLM`, `ANALYTICAL`, `GENERAL`).

### Standardization rule: one SOP per activity class, lab-wide

Rather than letting each team write its own SOP for "how do we log an
instrument run," the model enforces exactly one governing SOP per activity
class (culture maintenance, sampling, assay execution, instrument
operation), shared across all cell lines and all analytical methods. New
methods don't get a new SOP; they get a new `sop_version` of the existing
one, or a documented exception recorded in `sop_version.status`.

## 3. Metadata schema conventions

Every entity type that carries variable, method-specific metadata
(experiments, instrument runs, samples) uses a **schema-validated JSON
document** rather than sparse nullable columns. This keeps the core
relational schema in 3NF (no columns that are meaningful for only some
rows) while still letting each assay type define what it needs.

### Registry: `metadata_schema`

Each row registers one versioned schema:

| Column | Purpose |
|---|---|
| `entity_type` | Which table this governs (`EXPERIMENT`, `INSTRUMENT_RUN`, `SAMPLE`, `CELL_LINE`) |
| `schema_name` | Logical name, e.g. `experiment.titer` |
| `schema_version` | e.g. `v1` — schemas are versioned exactly like SOPs |
| `json_schema` | The actual JSON Schema (draft-07) document, stored as text |

The canonical copies also live on disk under `schemas/*.json` so they can
be reviewed, diffed, and version-controlled independently of the database
(`schemas/experiment_titer_v1.json`, `experiment_purity_v1.json`,
`instrument_run_v1.json`, `sample_v1.json`).

### Convention: `additionalProperties: false`

Every schema in this project sets `additionalProperties: false`. This is a
deliberate standardization choice: it forces anyone adding a new metadata
field to register a new schema version rather than silently appending
ad-hoc keys, which is exactly the kind of metadata drift that makes
lab-wide data capture inconsistent over time.

### Convention: required fields reflect what QA actually needs

Required fields on `experiment.titer.v1` (`assay_method`,
`dilution_factor`, `temperature_c`, `operator_initials`) were chosen because
they are the minimum set needed to reconstruct or challenge a titer result
during an investigation. The same principle applies to
`experiment.purity.v1` (needs `column_lot` — HPLC column lot is a common
root cause in purity OOS investigations) and `instrument_run.generic.v1`
(needs `run_id_on_instrument` so a result can be cross-referenced against
the instrument's own native data system).

### Validation is enforced at write time and at audit time

`metadata_validator.py` runs the same Draft-07 validation two ways:

1. **At audit/review time** (`validate_all`) — batch-validates every
   metadata row already in the database, used by `run_all.py` and by
   periodic data-quality reviews.
2. **At single-instance time** (`validate_single`) — used by tests and by
   what would be, in a real LIMS integration, the pre-insert validation
   hook that rejects a bad metadata payload before it ever reaches the
   database.

## 4. GxP governance checks

`gxp_governance.py` implements three checks a real quality-system periodic
review performs:

1. **Audit trail presence** — every `cell_line`, `sample`, `experiment`,
   and `instrument_run` row must have a matching `CREATE` entry in
   `audit_log`. This is checked by entity existence, not by trusting that
   the audit log is complete — i.e., it would catch a row inserted through
   a path that bypassed audit logging.
2. **Required-field enforcement (QC review)** — any `result` with a
   `qc_flag` other than `PASS` (i.e. `FAIL`, `OOS`, `OOT`) must have a
   non-null `reviewed_by` — a non-passing result cannot be "final" without
   a named QA reviewer, mirroring a real electronic-batch-record review
   gate.
3. **SOP-version linkage** — described above: no DRAFT SOPs governing real
   work, no activity outside its SOP's effective window, no cross-domain
   SOP misuse.

All three checks are re-run every time `run_all.py` executes, and are also
covered by `tests/test_pipeline.py`, which additionally injects a known
violation (a deleted audit-log row, an experiment pointed at a DRAFT SOP
version) to prove the checks actually catch what they claim to catch —
not just that they pass on already-clean data.
