"""RAW -> staging -> CLEAN processing and patient-level split assignment."""

import hashlib
import json
from typing import Any

from airflow.sdk.exceptions import AirflowFailException
from sqlalchemy import text

from diabetes_model.contract import (
    PATIENT_COLUMN,
    TARGET_CLASSES,
    TARGET_COLUMN,
    exclusion_reason,
    input_frame,
)
from diabetes_model.features import (
    TRANSFORMATION_VERSION,
    engineer_features,
    features_to_json,
)
from diabetes_model.splits import SPLIT_NAMES, SPLIT_SALT, assign_split
from diabetes_pipeline.settings import MAX_BATCH_SIZE, database_engine


def preprocess_to_staging(dag_run_id: str) -> dict[str, Any]:
    """Transform validated RAW records missing from CLEAN into the staging table.

    Reads RAW (never the CSV), applies the shared ``readmission-features-v2``
    rules in chunks of at most 15,000 records, and keeps excluded encounters
    with their reason so every validated record is accounted for in CLEAN.
    """
    select_pending = text(
        """
        SELECT r.raw_record_id, r.record_data
        FROM raw.records r
        WHERE r.status = 'validated'
          AND r.raw_record_id > :after_id
          AND NOT EXISTS (
              SELECT 1 FROM clean.records c
              WHERE c.raw_record_id = r.raw_record_id
                AND c.transformation_version = :transformation_version
          )
        ORDER BY r.raw_record_id
        LIMIT :limit
        """
    )
    insert_staging = text(
        """
        INSERT INTO clean.records_staging (
            dag_run_id, raw_record_id, transformation_version, features,
            target_readmitted, exclusion_reason
        )
        VALUES (
            :dag_run_id, :raw_record_id, :transformation_version,
            CAST(:features AS jsonb), :target_readmitted, :exclusion_reason
        )
        """
    )
    staged = excluded = 0
    last_id = 0
    engine = database_engine()
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM clean.records_staging WHERE dag_run_id = :dag_run_id"),
                {"dag_run_id": dag_run_id},
            )
        while True:
            with engine.begin() as connection:
                rows = connection.execute(
                    select_pending,
                    {
                        "after_id": last_id,
                        "transformation_version": TRANSFORMATION_VERSION,
                        "limit": MAX_BATCH_SIZE,
                    },
                ).mappings().all()
                if not rows:
                    break
                records = [row["record_data"] for row in rows]
                features = features_to_json(engineer_features(input_frame(records)))
                values = []
                for row, record, feature_values in zip(rows, records, features, strict=True):
                    reason = exclusion_reason(record)
                    excluded += reason is not None
                    values.append(
                        {
                            "dag_run_id": dag_run_id,
                            "raw_record_id": row["raw_record_id"],
                            "transformation_version": TRANSFORMATION_VERSION,
                            "features": json.dumps(feature_values, allow_nan=False),
                            "target_readmitted": record[TARGET_COLUMN],
                            "exclusion_reason": reason,
                        }
                    )
                connection.execute(insert_staging, values)
                staged += len(values)
                last_id = rows[-1]["raw_record_id"]
    finally:
        engine.dispose()
    return {
        "transformation_version": TRANSFORMATION_VERSION,
        "staged_rows": staged,
        "excluded_rows": excluded,
    }


def merge_staging_into_clean(dag_run_id: str) -> dict[str, Any]:
    """Atomically upsert this run's staged rows into CLEAN and clear staging."""
    engine = database_engine()
    try:
        with engine.begin() as connection:
            merged = connection.execute(
                text(
                    """
                    INSERT INTO clean.records (
                        raw_record_id, transformation_version, features,
                        target_readmitted, exclusion_reason
                    )
                    SELECT raw_record_id, transformation_version, features,
                           target_readmitted, exclusion_reason
                    FROM clean.records_staging
                    WHERE dag_run_id = :dag_run_id
                    ON CONFLICT (raw_record_id, transformation_version) DO UPDATE SET
                        features = EXCLUDED.features,
                        target_readmitted = EXCLUDED.target_readmitted,
                        exclusion_reason = EXCLUDED.exclusion_reason,
                        data_split = CASE
                            WHEN EXCLUDED.exclusion_reason IS NULL
                                THEN clean.records.data_split
                        END,
                        processed_at = CURRENT_TIMESTAMP
                    """
                ),
                {"dag_run_id": dag_run_id},
            ).rowcount
            connection.execute(
                text("DELETE FROM clean.records_staging WHERE dag_run_id = :dag_run_id"),
                {"dag_run_id": dag_run_id},
            )
            totals = connection.execute(
                text(
                    """
                    SELECT COUNT(*) AS total_rows,
                           COUNT(*) FILTER (WHERE exclusion_reason IS NULL) AS eligible_rows
                    FROM clean.records
                    WHERE transformation_version = :transformation_version
                    """
                ),
                {"transformation_version": TRANSFORMATION_VERSION},
            ).mappings().one()
    finally:
        engine.dispose()
    return {
        "merged_rows": merged,
        "clean_total_rows": totals["total_rows"],
        "clean_eligible_rows": totals["eligible_rows"],
    }


def assign_and_check_splits() -> dict[str, Any]:
    """Assign splits to new eligible CLEAN rows and verify the full partition.

    The split comes from a salted hash of ``patient_nbr`` (read from RAW
    lineage), so it never changes for rows assigned in earlier runs. Fails if a
    patient appears in more than one split.
    """
    engine = database_engine()
    try:
        with engine.begin() as connection:
            unassigned = connection.execute(
                text(
                    """
                    SELECT c.clean_record_id, r.record_data ->> :patient_column AS patient_id
                    FROM clean.records c
                    JOIN raw.records r ON r.raw_record_id = c.raw_record_id
                    WHERE c.transformation_version = :transformation_version
                      AND c.exclusion_reason IS NULL
                      AND c.data_split IS NULL
                    """
                ),
                {
                    "patient_column": PATIENT_COLUMN,
                    "transformation_version": TRANSFORMATION_VERSION,
                },
            ).mappings().all()
            if unassigned:
                connection.execute(
                    text(
                        """
                        UPDATE clean.records SET data_split = :data_split
                        WHERE clean_record_id = :clean_record_id
                        """
                    ),
                    [
                        {
                            "clean_record_id": row["clean_record_id"],
                            "data_split": assign_split(row["patient_id"]),
                        }
                        for row in unassigned
                    ],
                )

            leaked_patients = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT r.record_data ->> :patient_column
                        FROM clean.records c
                        JOIN raw.records r ON r.raw_record_id = c.raw_record_id
                        WHERE c.transformation_version = :transformation_version
                          AND c.data_split IS NOT NULL
                        GROUP BY 1
                        HAVING COUNT(DISTINCT c.data_split) > 1
                    ) leaked
                    """
                ),
                {
                    "patient_column": PATIENT_COLUMN,
                    "transformation_version": TRANSFORMATION_VERSION,
                },
            ).scalar_one()
            counts = connection.execute(
                text(
                    """
                    SELECT data_split, target_readmitted, COUNT(*) AS rows,
                           MAX(clean_record_id) AS max_id
                    FROM clean.records
                    WHERE transformation_version = :transformation_version
                      AND data_split IS NOT NULL
                    GROUP BY data_split, target_readmitted
                    """
                ),
                {"transformation_version": TRANSFORMATION_VERSION},
            ).mappings().all()
    finally:
        engine.dispose()

    if leaked_patients:
        raise AirflowFailException(
            f"{leaked_patients} patients appear in more than one data split."
        )
    split_counts = {
        split: {label: 0 for label in TARGET_CLASSES} for split in SPLIT_NAMES
    }
    for row in counts:
        split_counts[row["data_split"]][row["target_readmitted"]] = row["rows"]
    snapshot = {
        "transformation_version": TRANSFORMATION_VERSION,
        "split_salt": SPLIT_SALT,
        "rows": sum(row["rows"] for row in counts),
        "max_clean_record_id": max((row["max_id"] for row in counts), default=0),
        "split_counts": split_counts,
    }
    snapshot["fingerprint"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {"assigned_rows": len(unassigned), "snapshot": snapshot}
