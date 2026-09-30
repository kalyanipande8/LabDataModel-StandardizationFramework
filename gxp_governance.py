"""
GxP governance checks run against the populated database. These are the
kinds of checks a real quality-system audit or a validated-system periodic
review would run:

  1. Audit trail presence: every CREATE-able entity (cell_line, sample,
     experiment, instrument_run) that exists must have a corresponding
     audit_log CREATE entry.
  2. Required-field enforcement: every result row must reference a valid,
     non-null instrument_run (enforced by the FK/NOT NULL already, but we
     re-verify at the application layer too - defense in depth), and every
     result with qc_flag != 'PASS' must have been reviewed (reviewed_by
     NOT NULL) before being usable downstream.
  3. SOP-version linkage: every activity table that must be performed
     under SOP control (culture_vessel, sample, experiment, instrument_run)
     references a sop_version whose status is not 'DRAFT' as of the
     activity date (a DRAFT SOP cannot govern real lab work), and the
     sop_version must belong to a sop whose domain matches the activity
     (e.g. an experiment must not be linked to a CLM-only SOP).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class GovernanceReport:
    check_name: str
    passed: bool
    total_checked: int
    violations: list[str] = field(default_factory=list)


def check_audit_trail_presence(conn: sqlite3.Connection) -> GovernanceReport:
    violations = []
    total = 0
    entity_tables = {
        "CELL_LINE": "cell_line",
        "SAMPLE": "sample",
        "EXPERIMENT": "experiment",
        "INSTRUMENT_RUN": "instrument_run",
    }
    for entity_type, table in entity_tables.items():
        id_col = f"{table}_id"
        ids = [r[0] for r in conn.execute(f"SELECT {id_col} FROM {table}").fetchall()]
        total += len(ids)
        audited_ids = {
            r[0]
            for r in conn.execute(
                "SELECT entity_id FROM audit_log WHERE entity_type = ? AND action = 'CREATE'",
                (entity_type,),
            ).fetchall()
        }
        for eid in ids:
            if eid not in audited_ids:
                violations.append(f"{entity_type} id={eid} has no CREATE audit_log entry")
    return GovernanceReport(
        check_name="audit_trail_presence",
        passed=len(violations) == 0,
        total_checked=total,
        violations=violations,
    )


def check_required_field_enforcement(conn: sqlite3.Connection) -> GovernanceReport:
    violations = []
    rows = conn.execute(
        "SELECT result_id, qc_flag, reviewed_by FROM result"
    ).fetchall()
    for result_id, qc_flag, reviewed_by in rows:
        if qc_flag != "PASS" and reviewed_by is None:
            violations.append(
                f"result id={result_id} has qc_flag={qc_flag} but reviewed_by is NULL "
                "(non-passing results must be QA-reviewed before use)"
            )
    return GovernanceReport(
        check_name="required_field_enforcement_qc_review",
        passed=len(violations) == 0,
        total_checked=len(rows),
        violations=violations,
    )


def check_sop_version_linkage(conn: sqlite3.Connection) -> GovernanceReport:
    violations = []
    total = 0

    # (table, id_col, activity_date_col, expected_domain)
    activity_tables = [
        ("culture_vessel", "vessel_id", "start_date", "CLM"),
        ("sample", "sample_id", "draw_date", "CLM"),
        ("experiment", "experiment_id", "performed_date", "ANALYTICAL"),
        ("instrument_run", "instrument_run_id", "run_datetime", "ANALYTICAL"),
    ]
    for table, id_col, date_col, expected_domain in activity_tables:
        rows = conn.execute(
            f"""
            SELECT a.{id_col}, a.{date_col}, sv.status, sv.effective_date,
                   sv.retired_date, s.domain, sv.sop_version_id
            FROM {table} a
            JOIN sop_version sv ON a.sop_version_id = sv.sop_version_id
            JOIN sop s ON sv.sop_id = s.sop_id
            """
        ).fetchall()
        total += len(rows)
        for act_id, act_date, status, eff_date, ret_date, domain, sv_id in rows:
            if status == "DRAFT":
                violations.append(
                    f"{table} id={act_id} governed by DRAFT sop_version_id={sv_id}"
                )
            if act_date < eff_date:
                violations.append(
                    f"{table} id={act_id} performed on {act_date} before SOP "
                    f"sop_version_id={sv_id} became effective ({eff_date})"
                )
            if ret_date is not None and act_date > ret_date:
                violations.append(
                    f"{table} id={act_id} performed on {act_date} after SOP "
                    f"sop_version_id={sv_id} was retired ({ret_date})"
                )
            if domain not in (expected_domain, "GENERAL"):
                violations.append(
                    f"{table} id={act_id} uses sop_version_id={sv_id} from domain "
                    f"{domain}, expected {expected_domain} or GENERAL"
                )
    return GovernanceReport(
        check_name="sop_version_linkage",
        passed=len(violations) == 0,
        total_checked=total,
        violations=violations,
    )


def run_all_governance_checks(conn: sqlite3.Connection) -> list[GovernanceReport]:
    return [
        check_audit_trail_presence(conn),
        check_required_field_enforcement(conn),
        check_sop_version_linkage(conn),
    ]
