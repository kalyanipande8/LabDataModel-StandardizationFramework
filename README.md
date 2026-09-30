# LabDataModel-StandardizationFramework

Relational data model and standardization framework spanning **Cell-Line
Management (CLM)** and **Analytical Workflow** domains, with SOP-version
governance, JSON-schema metadata validation, and GxP audit-trail checks —
built and run against a real SQLite database.

> **Disclaimer:** This is a portfolio/demo project. All users, cell lines,
> samples, experiments, instrument runs, and results are **synthetically
> simulated** by `build_database.py` with a fixed random seed. No real
> molecule, program, instrument, or employer data is used. The relational
> design, normalization approach, SOP-versioning model, metadata-schema
> conventions, and GxP checks are real and are actually implemented and
> executed — only the underlying lab data is fictional.

## What it does

1. **`sql/schema.sql`** defines a normalized (3NF) relational schema, built
   for real into a SQLite database (`output/lab_data_model.db`):
   `role`, `lab_user`, `sop`, `sop_version`, `metadata_schema`, `cell_line`
   (self-referencing FK for parental ancestry), `culture_vessel`, `sample`,
   `sample_metadata`, `instrument`, `experiment`, `experiment_metadata`,
   `instrument_run`, `instrument_run_metadata`, `result`, `audit_log` — 16
   tables, real foreign keys, `PRAGMA foreign_keys = ON` enforced.

2. **`build_database.py`** populates it with realistic simulated data: 5
   users across 3 roles, 5 SOPs each with 2 versions (v1.0 retired / v2.0
   effective), 4 registered JSON-schema metadata definitions, 6 cell lines
   (3 parental + 3 derived subclones, via self-referencing FK), 12 culture
   vessels, 24 samples, 4 instruments, 40 experiments, and their downstream
   instrument runs and results.

3. **`metadata_validator.py`** validates every experiment/sample/instrument-
   run metadata document against its registered JSON Schema (Draft-07,
   via the real `jsonschema` library) — not a hand-rolled approximation.

4. **`gxp_governance.py`** runs three governance checks: audit-trail
   presence, required-field enforcement on non-passing QC results, and
   SOP-version linkage (no DRAFT SOPs governing real work, no activity
   outside its SOP's effective window, no cross-domain SOP misuse). See
   `docs/sop_standardization_guide.md` for the full rationale.

5. **`traceability_queries.py`** runs real cross-domain SQL joins that
   trace a `result` all the way back through `instrument_run` ->
   `instrument` -> `experiment` -> `sample` -> `culture_vessel` ->
   `cell_line` (including parental ancestry via the self-referencing FK).

6. **`run_all.py`** runs the whole pipeline end to end.

## Entity-Relationship overview

```
role ---< lab_user

sop ---< sop_version >--- lab_user (approved_by)

cell_line ---< cell_line (parental_line_id, self-referencing ancestry)
cell_line ---< culture_vessel >--- sop_version, lab_user (operator)
culture_vessel ---< sample >--- sop_version, lab_user (drawn_by)
sample ---< sample_metadata >--- metadata_schema

sample ---< experiment >--- sop_version, lab_user (performed_by)
experiment ---< experiment_metadata >--- metadata_schema

instrument ---< instrument_run
experiment ---< instrument_run >--- sop_version, lab_user (operator)
instrument_run ---< instrument_run_metadata >--- metadata_schema
instrument_run ---< result >--- lab_user (reviewed_by, nullable)

audit_log(entity_type, entity_id) -> generic reference to any of:
    cell_line | sample | experiment | instrument_run | result
```

Normalization notes: repeating/variable metadata (assay parameters,
instrument run parameters) is deliberately kept out of the core tables as
sparse columns and instead lives in `*_metadata` tables validated against
versioned JSON schemas — this avoids partial/transitive dependencies while
still supporting per-assay-type extensibility. SOP linkage is modeled as a
real foreign key to a versioned `sop_version` row (not a text column), so
"which SOP version governed this activity" is always a real, joinable fact.
Full column-level detail is in `sql/schema.sql`, which is heavily commented.

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_all.py
pytest -q
```

## Results

_All numbers below are copied directly from an actual run of `python
run_all.py` and `pytest -q` — nothing is hardcoded. The fixed random seed
(42) makes them reproducible._

### Database population

| Entity | Count |
|---|---|
| Roles | 3 |
| Users | 5 |
| SOPs | 5 (10 versions: 5 retired v1.0, 5 effective v2.0) |
| Registered metadata schemas | 4 |
| Cell lines | 6 (3 parental + 3 subclones) |
| Culture vessels | 12 |
| Samples | 24 (24 sample-metadata records) |
| Instruments | 4 |
| Experiments | 40 (30 with registered metadata) |
| Instrument runs | 53 (53 with registered metadata) |
| Results | 73 (4 non-PASS: OOS/OOT) |
| Audit log entries | 139 |

### Metadata schema validation

- **107/107 metadata records valid** against their registered Draft-07
  JSON Schema (24 sample + 30 experiment + 53 instrument-run metadata
  records).

### GxP governance checks

- **[PASS] audit_trail_presence** — 123 entities checked (cell lines,
  samples, experiments, instrument runs), 0 violations.
- **[PASS] required_field_enforcement_qc_review** — 73 results checked, 0
  violations (all 4 non-PASS results have a non-null `reviewed_by`).
- **[PASS] sop_version_linkage** — 129 SOP-governed activities checked
  (culture vessels, samples, experiments, instrument runs), 0 violations
  (no DRAFT SOPs governing work, no out-of-window activity, no cross-domain
  SOP misuse).

### Cross-domain traceability

- **73/73 results (100%) fully traceable** result -> instrument_run ->
  instrument -> experiment -> sample -> culture_vessel -> cell_line.
- Example single-result trace (`result_id=1`): `titer_g_per_L = 1.836 g/L`,
  `PASS`, measured on `INST-OCTET-01` (Octet BLI) in experiment `EXP-0001`
  (titer), sample `SMPL-0001`, shake-flask vessel, cell line
  `CHO-K1-PARENT-001`, governed by `SOP-ANA-001 v2.0`.
- Per-cell-line result rollup (top 3): `CHO-K1-PARENT-001` — 16 results (1
  non-PASS); `CHO-K1-SUBCLONE-003` — 12 results (2 non-PASS);
  `CHO-K1-SUBCLONE-002` — 12 results (0 non-PASS).

### Tests

```
14 passed in 0.36s
```

Tests cover: expected row counts after build; a real foreign-key violation
(bad `vessel_id` on `sample`) correctly raising `sqlite3.IntegrityError`; a
real required-field (`NOT NULL`) violation on `result.instrument_run_id`
correctly raising `IntegrityError`; a `UNIQUE` constraint violation on
`role.role_name`; metadata validation passing on the clean dataset and
correctly failing on a document missing a required field or carrying an
undeclared field; GxP checks passing on the clean dataset and correctly
flagging two injected violations (a deleted audit-log entry, an experiment
pointed at a DRAFT SOP version); full cross-domain traceability coverage;
and manual cross-checks confirming a traced result's experiment/sample/
cell-line codes match direct lookups exactly (not just that the join
returns *a* row).

## Repository layout

```
sql/schema.sql                     -- DDL, 16 tables, FKs, indexes, normalization notes
schemas/*.json                     -- versioned JSON-schema metadata definitions
build_database.py                  -- builds schema + simulates realistic data
metadata_validator.py              -- Draft-07 JSON-schema validation layer
gxp_governance.py                  -- audit trail / required-field / SOP-linkage checks
traceability_queries.py            -- cross-domain SQL traceability queries
run_all.py                         -- end-to-end pipeline runner
docs/sop_standardization_guide.md  -- SOP-versioning & metadata-schema conventions
tests/test_pipeline.py             -- 14 tests incl. injected constraint/governance violations
output/lab_data_model.db           -- generated SQLite database (gitignored, rebuild with run_all.py)
```
