"""
Real cross-domain traceability queries against the populated SQLite database:
result -> instrument_run -> instrument -> experiment -> sample ->
culture_vessel -> cell_line (-> parental cell_line), plus SOP-version
linkage at every governed step.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "output" / "lab_data_model.db"

FULL_TRACE_SQL = """
SELECT
    r.result_id,
    r.result_name,
    r.result_value,
    r.result_unit,
    r.qc_flag,
    ir.instrument_run_id,
    i.asset_tag        AS instrument_asset_tag,
    i.instrument_type,
    e.experiment_code,
    e.experiment_type,
    sm.sample_code,
    cv.vessel_id,
    cv.vessel_type,
    cl.cell_line_code,
    parent_cl.cell_line_code AS parental_cell_line_code,
    sop_exp.sop_code          AS experiment_sop_code,
    sv_exp.version_label      AS experiment_sop_version
FROM result r
JOIN instrument_run ir       ON r.instrument_run_id = ir.instrument_run_id
JOIN instrument i             ON ir.instrument_id = i.instrument_id
JOIN experiment e             ON ir.experiment_id = e.experiment_id
JOIN sample sm                ON e.sample_id = sm.sample_id
JOIN culture_vessel cv        ON sm.vessel_id = cv.vessel_id
JOIN cell_line cl              ON cv.cell_line_id = cl.cell_line_id
LEFT JOIN cell_line parent_cl  ON cl.parental_line_id = parent_cl.cell_line_id
JOIN sop_version sv_exp        ON e.sop_version_id = sv_exp.sop_version_id
JOIN sop sop_exp                ON sv_exp.sop_id = sop_exp.sop_id
WHERE r.result_id = ?
"""

FULL_TRACE_ALL_SQL = FULL_TRACE_SQL.replace("WHERE r.result_id = ?", "")


def trace_result_to_cell_line(conn: sqlite3.Connection, result_id: int) -> dict | None:
    """Trace one result all the way back to its originating cell line."""
    row = conn.execute(FULL_TRACE_SQL, (result_id,)).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute(FULL_TRACE_SQL, (result_id,)).description]
    return dict(zip(cols, row))


def full_traceability_count(conn: sqlite3.Connection) -> int:
    """Every result must be traceable to exactly one cell line - count rows."""
    return len(conn.execute(FULL_TRACE_ALL_SQL).fetchall())


def cell_line_result_summary(conn: sqlite3.Connection) -> list[tuple]:
    """Aggregate: for each cell line, how many results trace back to it, and
    how many of those failed QC - a real cross-domain analytical rollup."""
    sql = """
    SELECT
        cl.cell_line_code,
        COUNT(r.result_id) AS n_results,
        SUM(CASE WHEN r.qc_flag != 'PASS' THEN 1 ELSE 0 END) AS n_non_pass
    FROM cell_line cl
    JOIN culture_vessel cv ON cv.cell_line_id = cl.cell_line_id
    JOIN sample sm ON sm.vessel_id = cv.vessel_id
    JOIN experiment e ON e.sample_id = sm.sample_id
    JOIN instrument_run ir ON ir.experiment_id = e.experiment_id
    JOIN result r ON r.instrument_run_id = ir.instrument_run_id
    GROUP BY cl.cell_line_code
    ORDER BY n_results DESC
    """
    return conn.execute(sql).fetchall()


def subclone_ancestry_traceability(conn: sqlite3.Connection) -> list[tuple]:
    """For every subclone, trace results back through parental lineage -
    demonstrates the self-referencing FK ancestry chain is queryable."""
    sql = """
    SELECT
        cl.cell_line_code AS subclone,
        parent.cell_line_code AS parental_line,
        COUNT(r.result_id) AS n_results_on_subclone
    FROM cell_line cl
    JOIN cell_line parent ON cl.parental_line_id = parent.cell_line_id
    JOIN culture_vessel cv ON cv.cell_line_id = cl.cell_line_id
    JOIN sample sm ON sm.vessel_id = cv.vessel_id
    JOIN experiment e ON e.sample_id = sm.sample_id
    JOIN instrument_run ir ON ir.experiment_id = e.experiment_id
    JOIN result r ON r.instrument_run_id = ir.instrument_run_id
    GROUP BY cl.cell_line_code, parent.cell_line_code
    ORDER BY subclone
    """
    return conn.execute(sql).fetchall()


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    total_results = conn.execute("SELECT COUNT(*) FROM result").fetchone()[0]
    traceable = full_traceability_count(conn)
    print(f"Total results: {total_results}")
    print(f"Fully traceable results (result -> ... -> cell_line): {traceable}")

    first_result_id = conn.execute("SELECT MIN(result_id) FROM result").fetchone()[0]
    example = trace_result_to_cell_line(conn, first_result_id)
    print("\nExample single-result trace:")
    for k, v in example.items():
        print(f"  {k}: {v}")

    print("\nPer-cell-line result summary:")
    for row in cell_line_result_summary(conn):
        print(f"  {row}")

    print("\nSubclone ancestry traceability:")
    for row in subclone_ancestry_traceability(conn):
        print(f"  {row}")

    conn.close()
