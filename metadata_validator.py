"""
Real JSON-schema validation layer for experiment / instrument-run / sample
metadata, validated against the schema documents registered in the
`metadata_schema` table (and mirrored on disk under schemas/*.json).

Uses the `jsonschema` library (Draft-07) so validation is genuine, not a
hand-rolled approximation.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import jsonschema


@dataclass
class ValidationResult:
    entity_type: str
    entity_id: int
    schema_name: str
    schema_version: str
    is_valid: bool
    errors: list[str]


def load_schema_registry(conn: sqlite3.Connection) -> dict[int, dict]:
    """Load all registered metadata_schema rows keyed by metadata_schema_id."""
    cur = conn.execute(
        "SELECT metadata_schema_id, entity_type, schema_name, schema_version, json_schema "
        "FROM metadata_schema"
    )
    registry = {}
    for row in cur.fetchall():
        registry[row[0]] = {
            "entity_type": row[1],
            "schema_name": row[2],
            "schema_version": row[3],
            "json_schema": json.loads(row[4]),
        }
    return registry


def validate_metadata_table(
    conn: sqlite3.Connection,
    table_name: str,
    id_column: str,
    entity_type: str,
) -> list[ValidationResult]:
    """
    Validate every metadata_json row in the given *_metadata table against
    its registered JSON schema. Returns one ValidationResult per row.
    """
    registry = load_schema_registry(conn)
    cur = conn.execute(
        f"SELECT {id_column}, metadata_schema_id, metadata_json FROM {table_name}"
    )
    results: list[ValidationResult] = []
    for entity_id, schema_id, metadata_json in cur.fetchall():
        schema_entry = registry[schema_id]
        instance = json.loads(metadata_json)
        validator = jsonschema.Draft7Validator(schema_entry["json_schema"])
        errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
        error_msgs = [f"{list(e.path)}: {e.message}" for e in errors]
        results.append(
            ValidationResult(
                entity_type=entity_type,
                entity_id=entity_id,
                schema_name=schema_entry["schema_name"],
                schema_version=schema_entry["schema_version"],
                is_valid=len(error_msgs) == 0,
                errors=error_msgs,
            )
        )
    return results


def validate_all(conn: sqlite3.Connection) -> list[ValidationResult]:
    """Validate metadata across every governed entity type in the database."""
    all_results: list[ValidationResult] = []
    all_results += validate_metadata_table(
        conn, "sample_metadata", "sample_id", "SAMPLE"
    )
    all_results += validate_metadata_table(
        conn, "experiment_metadata", "experiment_id", "EXPERIMENT"
    )
    all_results += validate_metadata_table(
        conn, "instrument_run_metadata", "instrument_run_id", "INSTRUMENT_RUN"
    )
    return all_results


def validate_single(schema_json: dict, instance: dict) -> list[str]:
    """Validate a single instance against a single schema; return error messages."""
    validator = jsonschema.Draft7Validator(schema_json)
    return [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(instance)]
