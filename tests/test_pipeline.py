"""
Tests for the lab data model:
  - schema builds and populates without error, expected row counts
  - foreign-key constraint enforcement (a bad FK insert is rejected)
  - required-field / NOT NULL constraint enforcement is rejected
  - metadata validation catches a genuinely invalid metadata document
  - GxP governance checks pass on the good dataset and catch injected
    violations (audit trail missing, DRAFT SOP linkage)
  - cross-domain traceability query returns correct, real results
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import build_database
import gxp_governance
import metadata_validator
import traceability_queries as tq

TEST_DB = ROOT / "output" / "test_lab_data_model.db"


@pytest.fixture(scope="module")
def built_db():
    stats = build_database.build(TEST_DB)
    conn = sqlite3.connect(TEST_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn, stats
    conn.close()


def test_build_row_counts(built_db):
    conn, stats = built_db
    assert stats["n_cell_lines"] == 6
    assert stats["n_culture_vessels"] == 12
    assert stats["n_samples"] == 24
    assert stats["n_experiments"] == 40
    assert stats["n_results"] > 0
    assert conn.execute("SELECT COUNT(*) FROM cell_line").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 24
    assert conn.execute("SELECT COUNT(*) FROM experiment").fetchone()[0] == 40


def test_foreign_key_violation_is_rejected(built_db):
    """Sanity check: inserting a sample that references a non-existent vessel
    must be rejected by the real FK constraint (foreign_keys = ON)."""
    conn, _ = built_db
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sample (sample_id, sample_code, vessel_id, sample_type, "
            "draw_date, drawn_by, sop_version_id) VALUES (99999, 'BAD-SAMPLE', "
            "999999, 'supernatant', '2024-01-01', 1, 3)"
        )
    conn.rollback()


def test_required_field_violation_is_rejected(built_db):
    """A result missing its required instrument_run_id (NOT NULL) must be
    rejected."""
    conn, _ = built_db
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO result (result_id, instrument_run_id, result_name, "
            "result_value, result_unit, qc_flag) VALUES "
            "(999999, NULL, 'titer_g_per_L', 1.0, 'g/L', 'PASS')"
        )
    conn.rollback()


def test_role_name_uniqueness_enforced(built_db):
    """UNIQUE constraint on role_name must reject a duplicate."""
    conn, _ = built_db
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO role (role_id, role_name, can_approve_sop, description) "
            "VALUES (999, 'Scientist', 0, 'duplicate role name')"
        )
    conn.rollback()


def test_metadata_all_valid_on_good_dataset(built_db):
    conn, _ = built_db
    results = metadata_validator.validate_all(conn)
    assert len(results) > 0
    assert all(r.is_valid for r in results)


def test_metadata_validator_catches_real_invalid_document():
    """A metadata document missing a required field must fail validation -
    proves the validator is a real JSON-schema check, not a no-op."""
    schema = json.loads((ROOT / "schemas" / "experiment_titer_v1.json").read_text())
    bad_instance = {"assay_method": "ELISA"}  # missing dilution_factor, temperature_c, operator_initials
    errors = metadata_validator.validate_single(schema, bad_instance)
    assert len(errors) > 0

    good_instance = {
        "assay_method": "ELISA",
        "dilution_factor": 20,
        "temperature_c": 22.0,
        "operator_initials": "AB",
    }
    assert metadata_validator.validate_single(schema, good_instance) == []


def test_metadata_validator_rejects_additional_properties():
    schema = json.loads((ROOT / "schemas" / "sample_v1.json").read_text())
    bad_instance = {
        "storage_condition": "-80C",
        "volume_mL": 5.0,
        "collection_method": "pipette",
        "unexpected_field": "should not be allowed",
    }
    errors = metadata_validator.validate_single(schema, bad_instance)
    assert len(errors) > 0


def test_gxp_checks_pass_on_good_dataset(built_db):
    conn, _ = built_db
    reports = gxp_governance.run_all_governance_checks(conn)
    for report in reports:
        assert report.passed, f"{report.check_name} unexpectedly failed: {report.violations}"
        assert report.total_checked > 0


def test_gxp_audit_trail_check_catches_missing_entry(built_db):
    """Injected sanity check: delete one audit_log CREATE row for a real
    cell_line and confirm the governance check correctly flags it."""
    conn, _ = built_db
    row = conn.execute(
        "SELECT audit_log_id, entity_id FROM audit_log "
        "WHERE entity_type = 'CELL_LINE' AND action = 'CREATE' LIMIT 1"
    ).fetchone()
    audit_log_id, entity_id = row

    conn.execute("DELETE FROM audit_log WHERE audit_log_id = ?", (audit_log_id,))
    report = gxp_governance.check_audit_trail_presence(conn)
    assert not report.passed
    assert any(f"CELL_LINE id={entity_id}" in v for v in report.violations)

    # restore for other tests in this module
    conn.execute(
        "INSERT INTO audit_log VALUES (?, 'CELL_LINE', ?, 'CREATE', 2, '2024-01-01', 'restored for test isolation')",
        (audit_log_id, entity_id),
    )
    conn.commit()


def test_gxp_sop_linkage_catches_draft_sop(built_db):
    """Injected sanity check: point one experiment at a DRAFT sop_version
    and confirm the SOP-version-linkage governance check rejects it."""
    conn, _ = built_db
    draft_sv_id = conn.execute(
        "INSERT INTO sop_version (sop_id, version_label, effective_date, "
        "approved_by, status) VALUES (4, 'v3.0-draft', '2024-01-01', 5, 'DRAFT') "
        "RETURNING sop_version_id"
    ).fetchone()[0]

    experiment_id = conn.execute("SELECT experiment_id FROM experiment LIMIT 1").fetchone()[0]
    original_sv_id = conn.execute(
        "SELECT sop_version_id FROM experiment WHERE experiment_id = ?", (experiment_id,)
    ).fetchone()[0]

    conn.execute(
        "UPDATE experiment SET sop_version_id = ? WHERE experiment_id = ?",
        (draft_sv_id, experiment_id),
    )
    report = gxp_governance.check_sop_version_linkage(conn)
    assert not report.passed
    assert any(f"experiment id={experiment_id}" in v and "DRAFT" in v for v in report.violations)

    # restore
    conn.execute(
        "UPDATE experiment SET sop_version_id = ? WHERE experiment_id = ?",
        (original_sv_id, experiment_id),
    )
    conn.execute("DELETE FROM sop_version WHERE sop_version_id = ?", (draft_sv_id,))
    conn.commit()


def test_cross_domain_traceability_full_coverage(built_db):
    conn, stats = built_db
    total_results = conn.execute("SELECT COUNT(*) FROM result").fetchone()[0]
    traceable = tq.full_traceability_count(conn)
    assert traceable == total_results
    assert traceable == stats["n_results"]


def test_cross_domain_traceability_single_result_correctness(built_db):
    """Verify a specific result traces back to the correct sample, vessel,
    and cell line by cross-checking against direct lookups (not just that
    the join returns a row, but that the row is actually right)."""
    conn, _ = built_db
    result_id = conn.execute("SELECT MIN(result_id) FROM result").fetchone()[0]
    trace = tq.trace_result_to_cell_line(conn, result_id)
    assert trace is not None

    run_id = conn.execute(
        "SELECT instrument_run_id FROM result WHERE result_id = ?", (result_id,)
    ).fetchone()[0]
    assert trace["instrument_run_id"] == run_id

    exp_id = conn.execute(
        "SELECT experiment_id FROM instrument_run WHERE instrument_run_id = ?", (run_id,)
    ).fetchone()[0]
    expected_exp_code = conn.execute(
        "SELECT experiment_code FROM experiment WHERE experiment_id = ?", (exp_id,)
    ).fetchone()[0]
    assert trace["experiment_code"] == expected_exp_code

    sample_id = conn.execute(
        "SELECT sample_id FROM experiment WHERE experiment_id = ?", (exp_id,)
    ).fetchone()[0]
    expected_sample_code = conn.execute(
        "SELECT sample_code FROM sample WHERE sample_id = ?", (sample_id,)
    ).fetchone()[0]
    assert trace["sample_code"] == expected_sample_code

    vessel_id = conn.execute(
        "SELECT vessel_id FROM sample WHERE sample_id = ?", (sample_id,)
    ).fetchone()[0]
    cell_line_id = conn.execute(
        "SELECT cell_line_id FROM culture_vessel WHERE vessel_id = ?", (vessel_id,)
    ).fetchone()[0]
    expected_cl_code = conn.execute(
        "SELECT cell_line_code FROM cell_line WHERE cell_line_id = ?", (cell_line_id,)
    ).fetchone()[0]
    assert trace["cell_line_code"] == expected_cl_code


def test_subclone_ancestry_traceability_nonempty(built_db):
    conn, _ = built_db
    rows = tq.subclone_ancestry_traceability(conn)
    assert len(rows) > 0
    for subclone, parent, n_results in rows:
        assert subclone.startswith("CHO-K1-SUBCLONE")
        assert parent.startswith("CHO-K1-PARENT")
        assert n_results >= 0


def test_cell_line_result_summary_matches_manual_count(built_db):
    conn, _ = built_db
    summary = {row[0]: row[1] for row in tq.cell_line_result_summary(conn)}
    total_from_summary = sum(summary.values())
    total_results = conn.execute("SELECT COUNT(*) FROM result").fetchone()[0]
    assert total_from_summary == total_results
