-- ============================================================================
-- LabDataModel-StandardizationFramework
-- Relational schema spanning Cell-Line-Management (CLM) and Analytical
-- Workflow domains, with shared users/roles, SOP, and metadata governance.
--
-- Normalization notes (3NF):
--   * Every non-key attribute depends on the whole key and nothing but the
--     key (no partial or transitive dependencies).
--   * Repeating/variable metadata (instrument run parameters, experiment
--     conditions) is NOT stored as ad-hoc columns; it lives in the
--     EAV-style *_metadata tables validated against JSON-schema documents
--     in metadata_schema, keeping the core tables free of sparse/optional
--     columns while still allowing controlled extensibility.
--   * SOP versioning is modeled as its own entity (sop, sop_version) rather
--     than a free-text "sop_version" column, so a version is a real,
--     queryable, referenceable row (supports SOP-version linkage checks).
--   * Audit trail is a single append-only table (audit_log) referenced by
--     entity_type/entity_id rather than duplicating created_by/updated_by
--     audit columns across every table - avoids transitive dependency of
--     audit facts on business-table keys, and gives one place to enforce
--     "every mutating action produced a trail entry."
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- Users & Roles (shared across both domains, referenced by every activity
-- table so that GxP "who did what" traceability is possible everywhere)
-- ----------------------------------------------------------------------------

CREATE TABLE role (
    role_id         INTEGER PRIMARY KEY,
    role_name       TEXT NOT NULL UNIQUE,      -- e.g. 'Scientist', 'QA Reviewer', 'Lab Manager'
    can_approve_sop INTEGER NOT NULL DEFAULT 0 CHECK (can_approve_sop IN (0,1)),
    description     TEXT
);

CREATE TABLE lab_user (
    user_id         INTEGER PRIMARY KEY,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    role_id         INTEGER NOT NULL REFERENCES role(role_id),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    hire_date       TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- SOP standardization: an SOP is a governed document; each version is the
-- unit that experiments/instrument runs actually link against, so changing
-- an SOP never silently rewrites history.
-- ----------------------------------------------------------------------------

CREATE TABLE sop (
    sop_id          INTEGER PRIMARY KEY,
    sop_code        TEXT NOT NULL UNIQUE,      -- e.g. 'SOP-CLM-001'
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL CHECK (domain IN ('CLM','ANALYTICAL','GENERAL'))
);

CREATE TABLE sop_version (
    sop_version_id  INTEGER PRIMARY KEY,
    sop_id          INTEGER NOT NULL REFERENCES sop(sop_id),
    version_label   TEXT NOT NULL,             -- e.g. 'v2.1'
    effective_date  TEXT NOT NULL,
    retired_date    TEXT,
    approved_by     INTEGER NOT NULL REFERENCES lab_user(user_id),
    status          TEXT NOT NULL CHECK (status IN ('DRAFT','EFFECTIVE','RETIRED')),
    UNIQUE (sop_id, version_label)
);

-- ----------------------------------------------------------------------------
-- Metadata schema governance: defines, per (entity_type, schema_name), the
-- JSON-schema-style document that required/optional metadata fields for
-- that entity type must validate against. Versioned so schema evolution is
-- itself traceable.
-- ----------------------------------------------------------------------------

CREATE TABLE metadata_schema (
    metadata_schema_id INTEGER PRIMARY KEY,
    entity_type         TEXT NOT NULL CHECK (entity_type IN
                            ('EXPERIMENT','INSTRUMENT_RUN','SAMPLE','CELL_LINE')),
    schema_name          TEXT NOT NULL,
    schema_version       TEXT NOT NULL,
    json_schema          TEXT NOT NULL,        -- JSON Schema document (draft-07 style), as text
    UNIQUE (entity_type, schema_name, schema_version)
);

-- ----------------------------------------------------------------------------
-- CLM domain: cell line ancestry, culture vessels, samples drawn from them
-- ----------------------------------------------------------------------------

CREATE TABLE cell_line (
    cell_line_id     INTEGER PRIMARY KEY,
    cell_line_code   TEXT NOT NULL UNIQUE,     -- e.g. 'CHO-K1-CLM-014'
    host_organism    TEXT NOT NULL,
    parental_line_id INTEGER REFERENCES cell_line(cell_line_id), -- ancestry (self-FK)
    construct_name   TEXT NOT NULL,
    created_by       INTEGER NOT NULL REFERENCES lab_user(user_id),
    created_date     TEXT NOT NULL,
    sop_version_id   INTEGER NOT NULL REFERENCES sop_version(sop_version_id) -- clone-generation SOP
);

CREATE TABLE culture_vessel (
    vessel_id        INTEGER PRIMARY KEY,
    cell_line_id     INTEGER NOT NULL REFERENCES cell_line(cell_line_id),
    vessel_type      TEXT NOT NULL CHECK (vessel_type IN ('shake_flask','bioreactor','T-flask')),
    passage_number   INTEGER NOT NULL CHECK (passage_number >= 0),
    start_date       TEXT NOT NULL,
    end_date         TEXT,
    operator_id      INTEGER NOT NULL REFERENCES lab_user(user_id),
    sop_version_id   INTEGER NOT NULL REFERENCES sop_version(sop_version_id) -- culture-maintenance SOP
);

CREATE TABLE sample (
    sample_id        INTEGER PRIMARY KEY,
    sample_code      TEXT NOT NULL UNIQUE,     -- barcode-equivalent
    vessel_id        INTEGER NOT NULL REFERENCES culture_vessel(vessel_id),
    sample_type      TEXT NOT NULL CHECK (sample_type IN ('supernatant','cell_pellet','whole_broth')),
    draw_date        TEXT NOT NULL,
    drawn_by         INTEGER NOT NULL REFERENCES lab_user(user_id),
    sop_version_id   INTEGER NOT NULL REFERENCES sop_version(sop_version_id) -- sampling SOP
);

CREATE TABLE sample_metadata (
    sample_metadata_id INTEGER PRIMARY KEY,
    sample_id          INTEGER NOT NULL REFERENCES sample(sample_id),
    metadata_schema_id INTEGER NOT NULL REFERENCES metadata_schema(metadata_schema_id),
    metadata_json       TEXT NOT NULL,          -- validated against metadata_schema.json_schema
    UNIQUE (sample_id, metadata_schema_id)
);

-- ----------------------------------------------------------------------------
-- Analytical workflow domain: experiments performed on samples, instruments,
-- instrument runs, and the results they produce.
-- ----------------------------------------------------------------------------

CREATE TABLE instrument (
    instrument_id     INTEGER PRIMARY KEY,
    asset_tag         TEXT NOT NULL UNIQUE,
    instrument_type   TEXT NOT NULL,           -- e.g. 'HPLC', 'Plate Reader', 'Flow Cytometer'
    manufacturer      TEXT NOT NULL,
    model             TEXT NOT NULL,
    calibration_due   TEXT NOT NULL,
    location          TEXT NOT NULL
);

CREATE TABLE experiment (
    experiment_id     INTEGER PRIMARY KEY,
    experiment_code   TEXT NOT NULL UNIQUE,
    sample_id         INTEGER NOT NULL REFERENCES sample(sample_id),
    experiment_type   TEXT NOT NULL CHECK (experiment_type IN
                          ('titer','purity','identity','potency','glycan_profile')),
    performed_by      INTEGER NOT NULL REFERENCES lab_user(user_id),
    performed_date    TEXT NOT NULL,
    sop_version_id    INTEGER NOT NULL REFERENCES sop_version(sop_version_id) -- assay SOP
);

CREATE TABLE experiment_metadata (
    experiment_metadata_id INTEGER PRIMARY KEY,
    experiment_id           INTEGER NOT NULL REFERENCES experiment(experiment_id),
    metadata_schema_id      INTEGER NOT NULL REFERENCES metadata_schema(metadata_schema_id),
    metadata_json            TEXT NOT NULL,
    UNIQUE (experiment_id, metadata_schema_id)
);

CREATE TABLE instrument_run (
    instrument_run_id INTEGER PRIMARY KEY,
    experiment_id      INTEGER NOT NULL REFERENCES experiment(experiment_id),
    instrument_id      INTEGER NOT NULL REFERENCES instrument(instrument_id),
    run_datetime        TEXT NOT NULL,
    operator_id          INTEGER NOT NULL REFERENCES lab_user(user_id),
    sop_version_id       INTEGER NOT NULL REFERENCES sop_version(sop_version_id) -- instrument-operation SOP
);

CREATE TABLE instrument_run_metadata (
    instrument_run_metadata_id INTEGER PRIMARY KEY,
    instrument_run_id           INTEGER NOT NULL REFERENCES instrument_run(instrument_run_id),
    metadata_schema_id          INTEGER NOT NULL REFERENCES metadata_schema(metadata_schema_id),
    metadata_json                TEXT NOT NULL,
    UNIQUE (instrument_run_id, metadata_schema_id)
);

CREATE TABLE result (
    result_id          INTEGER PRIMARY KEY,
    instrument_run_id  INTEGER NOT NULL REFERENCES instrument_run(instrument_run_id),
    result_name        TEXT NOT NULL,          -- e.g. '%monomer', 'titer_g_per_L'
    result_value        REAL NOT NULL,
    result_unit          TEXT NOT NULL,
    qc_flag               TEXT NOT NULL DEFAULT 'PASS' CHECK (qc_flag IN ('PASS','FAIL','OOS','OOT')),
    reviewed_by           INTEGER REFERENCES lab_user(user_id),   -- NULL until QA review (GxP check target)
    reviewed_date          TEXT
);

-- ----------------------------------------------------------------------------
-- Audit trail: append-only log referenced generically by entity_type/id.
-- GxP governance requires every mutating action leave a trail entry here.
-- ----------------------------------------------------------------------------

CREATE TABLE audit_log (
    audit_log_id     INTEGER PRIMARY KEY,
    entity_type      TEXT NOT NULL,
    entity_id        INTEGER NOT NULL,
    action           TEXT NOT NULL CHECK (action IN ('CREATE','UPDATE','REVIEW')),
    performed_by     INTEGER NOT NULL REFERENCES lab_user(user_id),
    performed_at     TEXT NOT NULL,
    detail           TEXT
);

CREATE INDEX idx_sample_vessel ON sample(vessel_id);
CREATE INDEX idx_vessel_cellline ON culture_vessel(cell_line_id);
CREATE INDEX idx_experiment_sample ON experiment(sample_id);
CREATE INDEX idx_run_experiment ON instrument_run(experiment_id);
CREATE INDEX idx_run_instrument ON instrument_run(instrument_id);
CREATE INDEX idx_result_run ON result(instrument_run_id);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
