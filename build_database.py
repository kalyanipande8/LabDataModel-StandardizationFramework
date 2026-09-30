"""
Builds a real SQLite database from sql/schema.sql, then populates it with
realistic simulated CLM + analytical-workflow data:

  5 users, 3 roles, 4 SOPs (each with 2 versions), 6 cell lines (with
  parental ancestry), 12 culture vessels, 24 samples, 40 experiments,
  4 instruments, 60 instrument runs, ~120 results, plus metadata rows on
  samples/experiments/instrument runs validated against schemas/*.json,
  and a matching audit_log trail.

Deterministic (seeded) so numbers in README/docs are reproducible.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "output" / "lab_data_model.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
SCHEMAS_DIR = ROOT / "schemas"

random.seed(42)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def load_json_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


def populate(conn: sqlite3.Connection) -> dict:
    """Populate the database; returns a dict of key stats for reporting."""
    cur = conn.cursor()
    base_date = datetime(2024, 1, 1)

    # ---- roles & users -----------------------------------------------
    roles = [
        (1, "Scientist", 0, "Bench scientist executing experiments and sampling"),
        (2, "QA Reviewer", 1, "Reviews and approves SOPs and non-passing results"),
        (3, "Lab Manager", 1, "Owns SOP program, approves SOP versions"),
    ]
    cur.executemany("INSERT INTO role VALUES (?,?,?,?)", roles)

    users = [
        (1, "Alice Nguyen", "alice.nguyen@lab.example", 1, 1, "2021-03-01"),
        (2, "Ben Ortiz", "ben.ortiz@lab.example", 1, 1, "2020-06-15"),
        (3, "Carla Singh", "carla.singh@lab.example", 2, 1, "2019-01-10"),
        (4, "Derek Wu", "derek.wu@lab.example", 1, 1, "2022-09-01"),
        (5, "Elena Petrova", "elena.petrova@lab.example", 3, 1, "2017-04-20"),
    ]
    cur.executemany("INSERT INTO lab_user VALUES (?,?,?,?,?,?)", users)

    # ---- SOPs & versions -----------------------------------------------
    sops = [
        (1, "SOP-CLM-001", "Cell Line Generation and Clone Selection", "CLM"),
        (2, "SOP-CLM-002", "Bioreactor and Shake Flask Culture Maintenance", "CLM"),
        (3, "SOP-CLM-003", "Sample Draw and Labeling", "CLM"),
        (4, "SOP-ANA-001", "Titer and Purity Assay Execution", "ANALYTICAL"),
        (5, "SOP-ANA-002", "Instrument Operation and Run Logging", "ANALYTICAL"),
    ]
    cur.executemany("INSERT INTO sop VALUES (?,?,?,?)", sops)

    sop_versions = []
    sv_id = 1
    sop_version_map = {}  # sop_id -> list of sop_version_id (chronological)
    for sop_id, _, _, _ in sops:
        for v_label, eff, ret, status in [
            ("v1.0", "2022-01-01", "2023-12-31", "RETIRED"),
            ("v2.0", "2024-01-01", None, "EFFECTIVE"),
        ]:
            sop_versions.append((sv_id, sop_id, v_label, eff, ret, 5, status))
            sop_version_map.setdefault(sop_id, []).append(sv_id)
            sv_id += 1
    cur.executemany("INSERT INTO sop_version VALUES (?,?,?,?,?,?,?)", sop_versions)

    def current_sv(sop_id: int) -> int:
        return sop_version_map[sop_id][1]  # v2.0, effective 2024-01-01+

    # ---- metadata schemas ------------------------------------------------
    metadata_schemas = [
        (1, "SAMPLE", "sample.generic", "v1", json.dumps(load_json_schema("sample_v1.json"))),
        (2, "EXPERIMENT", "experiment.titer", "v1", json.dumps(load_json_schema("experiment_titer_v1.json"))),
        (3, "EXPERIMENT", "experiment.purity", "v1", json.dumps(load_json_schema("experiment_purity_v1.json"))),
        (4, "INSTRUMENT_RUN", "instrument_run.generic", "v1", json.dumps(load_json_schema("instrument_run_v1.json"))),
    ]
    cur.executemany("INSERT INTO metadata_schema VALUES (?,?,?,?,?)", metadata_schemas)

    audit_rows = []
    audit_id = 1

    def audit(entity_type, entity_id, action, performed_by, performed_at, detail=""):
        nonlocal audit_id
        audit_rows.append((audit_id, entity_type, entity_id, action, performed_by, performed_at, detail))
        audit_id += 1

    # ---- cell lines (with ancestry) ---------------------------------------
    cell_lines = []
    cl_id = 1
    parental_ids = {}
    for i in range(1, 4):  # 3 parental lines
        code = f"CHO-K1-PARENT-{i:03d}"
        d = base_date + timedelta(days=i)
        cell_lines.append((cl_id, code, "CHO-K1", None, f"pAAV-construct-{i}", 2, iso(d), current_sv(1)))
        audit("CELL_LINE", cl_id, "CREATE", 2, iso(d), "parental clone isolation")
        parental_ids[cl_id] = cl_id
        cl_id += 1
    for i in range(1, 4):  # 3 derived subclones
        parent = i  # derive from parental i
        code = f"CHO-K1-SUBCLONE-{i:03d}"
        d = base_date + timedelta(days=10 + i)
        cell_lines.append((cl_id, code, "CHO-K1", parent, f"pAAV-construct-{i}-sc", 4, iso(d), current_sv(1)))
        audit("CELL_LINE", cl_id, "CREATE", 4, iso(d), "subclone derived from parental line")
        cl_id += 1
    cur.executemany("INSERT INTO cell_line VALUES (?,?,?,?,?,?,?,?)", cell_lines)

    # ---- culture vessels ----------------------------------------------
    vessels = []
    v_id = 1
    vessel_ids_by_cellline = {}
    for cell_line_row in cell_lines:
        this_cl_id = cell_line_row[0]
        for rep in range(2):  # 2 vessels per cell line = 12 total
            vtype = "shake_flask" if rep == 0 else "bioreactor"
            d = base_date + timedelta(days=20 + this_cl_id + rep)
            end_d = d + timedelta(days=14)
            operator = random.choice([1, 2, 4])
            vessels.append((v_id, this_cl_id, vtype, rep, iso(d), iso(end_d), operator, current_sv(2)))
            audit("CULTURE_VESSEL", v_id, "CREATE", operator, iso(d), f"{vtype} culture start")
            vessel_ids_by_cellline.setdefault(this_cl_id, []).append(v_id)
            v_id += 1
    cur.executemany("INSERT INTO culture_vessel VALUES (?,?,?,?,?,?,?,?)", vessels)

    # ---- samples (with metadata) ----------------------------------------
    samples = []
    sample_metadata_rows = []
    s_id = 1
    sm_id = 1
    sample_ids_by_vessel = {}
    for vessel_row in vessels:
        this_v_id = vessel_row[0]
        for rep in range(2):  # 2 samples per vessel = 24 total
            d = base_date + timedelta(days=25 + this_v_id + rep)
            drawer = random.choice([1, 2, 4])
            sample_type = random.choice(["supernatant", "cell_pellet", "whole_broth"])
            code = f"SMPL-{s_id:04d}"
            samples.append((s_id, code, this_v_id, sample_type, iso(d), drawer, current_sv(3)))
            audit("SAMPLE", s_id, "CREATE", drawer, iso(d), "sample drawn per SOP-CLM-003")

            metadata = {
                "storage_condition": random.choice(["-80C", "-20C", "4C"]),
                "volume_mL": round(random.uniform(0.5, 50.0), 2),
                "collection_method": "aseptic pipette draw",
            }
            sample_metadata_rows.append((sm_id, s_id, 1, json.dumps(metadata)))
            sm_id += 1

            sample_ids_by_vessel.setdefault(this_v_id, []).append(s_id)
            s_id += 1
    cur.executemany("INSERT INTO sample VALUES (?,?,?,?,?,?,?)", samples)
    cur.executemany("INSERT INTO sample_metadata VALUES (?,?,?,?)", sample_metadata_rows)

    # ---- instruments -------------------------------------------------
    instruments = [
        (1, "INST-HPLC-01", "HPLC", "Agilent", "1260 Infinity II", "2026-06-01", "Analytical Lab A"),
        (2, "INST-HPLC-02", "HPLC", "Waters", "Alliance e2695", "2026-03-15", "Analytical Lab A"),
        (3, "INST-OCTET-01", "Octet BLI", "Sartorius", "Octet R8", "2026-08-01", "Analytical Lab B"),
        (4, "INST-CE-01", "CE-SDS", "SCIEX", "PA 800 Plus", "2026-05-20", "Analytical Lab B"),
    ]
    cur.executemany("INSERT INTO instrument VALUES (?,?,?,?,?,?,?)", instruments)

    # ---- experiments (with metadata) ----------------------------------
    experiments = []
    experiment_metadata_rows = []
    e_id = 1
    em_id = 1
    experiment_ids = []
    all_sample_ids = [row[0] for row in samples]
    exp_types_weighted = ["titer"] * 15 + ["purity"] * 15 + ["identity"] * 5 + ["potency"] * 3 + ["glycan_profile"] * 2

    for i in range(40):
        exp_type = exp_types_weighted[i % len(exp_types_weighted)]
        this_sample_id = all_sample_ids[i % len(all_sample_ids)]
        performer = random.choice([1, 2, 4])
        d = base_date + timedelta(days=40 + i)
        code = f"EXP-{e_id:04d}"
        experiments.append((e_id, code, this_sample_id, exp_type, performer, iso(d), current_sv(4)))
        audit("EXPERIMENT", e_id, "CREATE", performer, iso(d), f"{exp_type} assay per SOP-ANA-001")

        if exp_type == "titer":
            metadata = {
                "assay_method": random.choice(["Protein A HPLC", "Octet BLI", "ELISA"]),
                "dilution_factor": random.choice([10, 20, 50, 100]),
                "temperature_c": round(random.uniform(18, 25), 1),
                "operator_initials": "".join([n[0] for n in ["A", "N"]]),
            }
            experiment_metadata_rows.append((em_id, e_id, 2, json.dumps(metadata)))
            em_id += 1
        elif exp_type == "purity":
            metadata = {
                "assay_method": random.choice(["SEC-HPLC", "CE-SDS", "RP-HPLC"]),
                "column_lot": f"COL-{random.randint(1000,9999)}",
                "mobile_phase": "100mM sodium phosphate, pH 6.8",
                "operator_initials": "BO",
            }
            experiment_metadata_rows.append((em_id, e_id, 3, json.dumps(metadata)))
            em_id += 1
        # identity/potency/glycan_profile experiments intentionally carry no
        # extra governed metadata in this dataset (schema only defined for
        # titer/purity) - demonstrates that metadata governance is optional
        # per entity, not universally mandatory.

        experiment_ids.append(e_id)
        e_id += 1
    cur.executemany("INSERT INTO experiment VALUES (?,?,?,?,?,?,?)", experiments)
    cur.executemany("INSERT INTO experiment_metadata VALUES (?,?,?,?)", experiment_metadata_rows)

    # ---- instrument runs (with metadata) + results --------------------
    instrument_runs = []
    run_metadata_rows = []
    results = []
    r_id = 1
    run_id = 1
    rm_id = 1

    result_specs = {
        "titer": [("titer_g_per_L", (0.5, 6.0), "g/L")],
        "purity": [("%monomer", (90.0, 99.5), "%"), ("%HMW", (0.1, 8.0), "%")],
        "identity": [("identity_match_score", (0.85, 1.0), "score")],
        "potency": [("relative_potency_pct", (70.0, 130.0), "%")],
        "glycan_profile": [("%afucosylation", (2.0, 15.0), "%")],
    }
    instrument_for_type = {
        "titer": [1, 3],
        "purity": [1, 2, 4],
        "identity": [2, 4],
        "potency": [1, 3],
        "glycan_profile": [2],
    }

    for exp_row in experiments:
        this_e_id, _, _, exp_type, performer, exp_date, _ = exp_row
        n_runs = 1 if exp_type in ("identity", "potency") else random.choice([1, 2])
        for _run in range(n_runs):
            instrument_id = random.choice(instrument_for_type[exp_type])
            run_dt = f"{exp_date}T{random.randint(8,17):02d}:{random.randint(0,59):02d}:00"
            operator = random.choice([1, 2, 4])
            instrument_runs.append((run_id, this_e_id, instrument_id, run_dt, operator, current_sv(5)))
            audit("INSTRUMENT_RUN", run_id, "CREATE", operator, exp_date, "run logged per SOP-ANA-002")

            metadata = {
                "software_version": random.choice(["3.4.1", "3.5.0", "2.9.2"]),
                "run_id_on_instrument": f"RUN-{random.randint(10000,99999)}",
                "sample_injection_volume_uL": round(random.uniform(5, 100), 1),
            }
            run_metadata_rows.append((rm_id, run_id, 4, json.dumps(metadata)))
            rm_id += 1

            for result_name, (lo, hi), unit in result_specs[exp_type]:
                value = round(random.uniform(lo, hi), 3)
                # inject a small, realistic rate of QC excursions
                qc_flag = "PASS"
                if random.random() < 0.08:
                    qc_flag = random.choice(["OOS", "OOT"])
                reviewed_by = None
                reviewed_date = None
                if qc_flag != "PASS":
                    reviewed_by = 3  # Carla Singh, QA Reviewer
                    reviewed_date = iso(datetime.strptime(exp_date, "%Y-%m-%d") + timedelta(days=2))
                    audit("RESULT", r_id, "REVIEW", 3, reviewed_date, f"QA review of {qc_flag} result")
                results.append(
                    (r_id, run_id, result_name, value, unit, qc_flag, reviewed_by, reviewed_date)
                )
                r_id += 1
            run_id += 1

    cur.executemany("INSERT INTO instrument_run VALUES (?,?,?,?,?,?)", instrument_runs)
    cur.executemany("INSERT INTO instrument_run_metadata VALUES (?,?,?,?)", run_metadata_rows)
    cur.executemany("INSERT INTO result VALUES (?,?,?,?,?,?,?,?)", results)

    cur.executemany("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?)", audit_rows)

    conn.commit()

    return {
        "n_roles": len(roles),
        "n_users": len(users),
        "n_sops": len(sops),
        "n_sop_versions": len(sop_versions),
        "n_metadata_schemas": len(metadata_schemas),
        "n_cell_lines": len(cell_lines),
        "n_culture_vessels": len(vessels),
        "n_samples": len(samples),
        "n_sample_metadata": len(sample_metadata_rows),
        "n_instruments": len(instruments),
        "n_experiments": len(experiments),
        "n_experiment_metadata": len(experiment_metadata_rows),
        "n_instrument_runs": len(instrument_runs),
        "n_instrument_run_metadata": len(run_metadata_rows),
        "n_results": len(results),
        "n_results_non_pass": sum(1 for r in results if r[5] != "PASS"),
        "n_audit_log_rows": len(audit_rows),
    }


def build(db_path: Path = DB_PATH) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    build_schema(conn)
    stats = populate(conn)
    conn.close()
    return stats


if __name__ == "__main__":
    stats = build()
    print(f"Database built at {DB_PATH}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
