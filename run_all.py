"""Runs the full pipeline: build DB -> populate -> validate metadata ->
run GxP governance checks -> run cross-domain traceability queries."""
import sqlite3

import build_database
import gxp_governance
import metadata_validator
import traceability_queries as tq


def main() -> None:
    print("=" * 70)
    print("1. Building database and simulating data")
    print("=" * 70)
    stats = build_database.build()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    conn = sqlite3.connect(build_database.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    print()
    print("=" * 70)
    print("2. Metadata schema validation")
    print("=" * 70)
    results = metadata_validator.validate_all(conn)
    n_valid = sum(1 for r in results if r.is_valid)
    print(f"  {n_valid}/{len(results)} metadata records valid against their registered JSON schema")
    for r in results:
        if not r.is_valid:
            print(f"  INVALID: {r.entity_type} id={r.entity_id} schema={r.schema_name}/{r.schema_version}: {r.errors}")

    print()
    print("=" * 70)
    print("3. GxP governance checks")
    print("=" * 70)
    reports = gxp_governance.run_all_governance_checks(conn)
    for report in reports:
        status = "PASS" if report.passed else "FAIL"
        print(f"  [{status}] {report.check_name}: {report.total_checked} checked, {len(report.violations)} violations")
        for v in report.violations[:5]:
            print(f"      - {v}")

    print()
    print("=" * 70)
    print("4. Cross-domain traceability")
    print("=" * 70)
    total_results = conn.execute("SELECT COUNT(*) FROM result").fetchone()[0]
    traceable = tq.full_traceability_count(conn)
    print(f"  {traceable}/{total_results} results fully traceable result -> instrument_run -> "
          f"instrument -> experiment -> sample -> culture_vessel -> cell_line")

    first_result_id = conn.execute("SELECT MIN(result_id) FROM result").fetchone()[0]
    example = tq.trace_result_to_cell_line(conn, first_result_id)
    print(f"\n  Example trace for result_id={first_result_id}:")
    for k, v in example.items():
        print(f"      {k}: {v}")

    print("\n  Per-cell-line result summary (top 3):")
    for row in tq.cell_line_result_summary(conn)[:3]:
        print(f"      {row}")

    conn.close()
    print()
    print("Done.")


if __name__ == "__main__":
    main()
