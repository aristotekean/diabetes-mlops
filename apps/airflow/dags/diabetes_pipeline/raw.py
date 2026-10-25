"""Source file checks, incremental RAW ingestion, and RAW quality validation."""

import csv
import hashlib
import itertools
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from airflow.sdk.exceptions import AirflowFailException
from sqlalchemy import bindparam, text

from diabetes_model.contract import ENCOUNTER_COLUMN, SOURCE_COLUMNS, validate_source_record
from diabetes_pipeline.settings import (
    DATASET_URL,
    MAX_BATCH_SIZE,
    MAX_REJECTED_FRACTION,
    database_engine,
)

BATCH_NAMESPACE = uuid.UUID("81ef2d31-3813-4c32-a9c2-310b3bc590ef")
DUPLICATE_ENCOUNTER = "duplicate_encounter_id"


def ensure_dataset_available(path: Path) -> None:
    """Download the workshop source into its persistent mount when absent."""
    if path.is_file():
        return
    from scripts.download_diabetes_dataset import download_dataset

    download_dataset(destination=path, url=DATASET_URL)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_metadata(path: Path) -> dict[str, Any]:
    """Validate the file's schema and row shape; row content is checked in RAW."""
    if not path.is_file():
        raise AirflowFailException(f"Dataset file is unavailable: {path}")
    if path.stat().st_size == 0:
        raise AirflowFailException(f"Dataset file is empty: {path}")

    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise AirflowFailException("Dataset CSV has no header.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise AirflowFailException("Dataset CSV has duplicate column names.")
        missing_columns = set(SOURCE_COLUMNS).difference(reader.fieldnames)
        extra_columns = set(reader.fieldnames).difference(SOURCE_COLUMNS)
        if missing_columns or extra_columns:
            raise AirflowFailException(
                "Dataset schema mismatch. "
                f"Missing columns: {sorted(missing_columns)}; "
                f"unexpected columns: {sorted(extra_columns)}"
            )
        for row_number, row in enumerate(reader, start=1):
            if None in row or any(value is None for value in row.values()):
                raise AirflowFailException(
                    f"CSV row {row_number} does not have one value per column."
                )
            row_count += 1

    if row_count == 0:
        raise AirflowFailException("Dataset CSV contains no data rows.")
    return {
        "path": str(path),
        "source_file": path.name,
        "source_uri": DATASET_URL,
        "source_sha256": _sha256_file(path),
        "row_count": row_count,
        "total_batches": (row_count + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE,
    }


def _hash_record(record: dict[str, str]) -> str:
    canonical_record = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()


def ingest_next_batch(source: dict[str, Any], dag_run_id: str) -> dict[str, Any]:
    """Load the first batch of this source snapshot that is not completed yet.

    One batch per DAG run simulates incremental arrival. A batch left
    ``loading`` or ``failed`` by an earlier run is resumed before a new one.
    """
    engine = database_engine()
    try:
        with engine.connect() as connection:
            completed = set(
                connection.execute(
                    text(
                        """
                        SELECT batch_number FROM raw.load_batches
                        WHERE source_sha256 = :source_sha256 AND status = 'completed'
                        """
                    ),
                    {"source_sha256": source["source_sha256"]},
                ).scalars()
            )
        pending = [
            number
            for number in range(1, source["total_batches"] + 1)
            if number not in completed
        ]
        if not pending:
            return {"batch_number": None, "inserted_rows": 0, "pending_batches": 0}

        batch_number = pending[0]
        first_row_number = (batch_number - 1) * MAX_BATCH_SIZE + 1
        with Path(source["path"]).open("r", encoding="utf-8-sig", newline="") as dataset:
            rows = list(
                itertools.islice(
                    csv.DictReader(dataset),
                    first_row_number - 1,
                    first_row_number - 1 + MAX_BATCH_SIZE,
                )
            )
        inserted_rows = _insert_raw_batch(
            engine,
            source=source,
            batch_number=batch_number,
            first_row_number=first_row_number,
            rows=rows,
            dag_run_id=dag_run_id,
        )
    finally:
        engine.dispose()

    return {
        "batch_number": batch_number,
        "first_row_number": first_row_number,
        "record_count": len(rows),
        "inserted_rows": inserted_rows,
        "pending_batches": len(pending) - 1,
    }


def _insert_raw_batch(
    engine: Any,
    *,
    source: dict[str, Any],
    batch_number: int,
    first_row_number: int,
    rows: list[dict[str, str]],
    dag_run_id: str,
) -> int:
    """Persist or resume one atomic, idempotent raw batch."""
    batch_id = uuid.uuid5(BATCH_NAMESPACE, f"{source['source_sha256']}:{batch_number}")
    batch_size = len(rows)
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"Invalid raw batch size: {batch_size}")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO raw.load_batches (
                    batch_id, source_file, source_uri, source_sha256,
                    batch_number, expected_record_count, status, dag_run_id
                )
                VALUES (
                    :batch_id, :source_file, :source_uri, :source_sha256,
                    :batch_number, :expected_record_count, 'loading', :dag_run_id
                )
                ON CONFLICT (source_sha256, batch_number) DO NOTHING
                """
            ),
            {
                "batch_id": batch_id,
                "source_file": source["source_file"],
                "source_uri": source["source_uri"],
                "source_sha256": source["source_sha256"],
                "batch_number": batch_number,
                "expected_record_count": batch_size,
                "dag_run_id": dag_run_id,
            },
        )
        batch = connection.execute(
            text(
                """
                SELECT batch_id, expected_record_count, loaded_record_count, status
                FROM raw.load_batches
                WHERE source_sha256 = :source_sha256 AND batch_number = :batch_number
                FOR UPDATE
                """
            ),
            {"source_sha256": source["source_sha256"], "batch_number": batch_number},
        ).mappings().one()
        if batch["batch_id"] != batch_id or batch["expected_record_count"] != batch_size:
            raise RuntimeError(
                f"Existing batch {batch_number} metadata conflicts with this source."
            )
        if batch["status"] == "completed":
            if batch["loaded_record_count"] != batch_size:
                raise RuntimeError(
                    f"Completed batch {batch_number} has an invalid record count."
                )
            return 0
        connection.execute(
            text(
                """
                UPDATE raw.load_batches
                SET status = 'loading', completed_at = NULL, error_message = NULL,
                    dag_run_id = :dag_run_id
                WHERE batch_id = :batch_id
                """
            ),
            {"batch_id": batch_id, "dag_run_id": dag_run_id},
        )

    record_values = [
        {
            "batch_id": batch_id,
            "source_sha256": source["source_sha256"],
            "source_row_number": first_row_number + offset,
            "source_record_sha256": _hash_record(record),
            "encounter_id": record[ENCOUNTER_COLUMN],
            "record_data": json.dumps(record, ensure_ascii=False),
        }
        for offset, record in enumerate(rows)
    ]
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO raw.records (
                        batch_id, source_sha256, source_row_number,
                        source_record_sha256, encounter_id, record_data
                    )
                    VALUES (
                        :batch_id, :source_sha256, :source_row_number,
                        :source_record_sha256, :encounter_id,
                        CAST(:record_data AS jsonb)
                    )
                    ON CONFLICT (source_sha256, source_row_number) DO NOTHING
                    """
                ),
                record_values,
            )
            loaded_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM raw.records
                    WHERE source_sha256 = :source_sha256
                      AND source_row_number BETWEEN :first_row_number AND :last_row_number
                    """
                ),
                {
                    "source_sha256": source["source_sha256"],
                    "first_row_number": first_row_number,
                    "last_row_number": first_row_number + batch_size - 1,
                },
            ).scalar_one()
            if loaded_count != batch_size:
                raise RuntimeError(
                    f"Batch {batch_number} expected {batch_size} raw records, "
                    f"found {loaded_count}."
                )
            connection.execute(
                text(
                    """
                    UPDATE raw.load_batches
                    SET loaded_record_count = :loaded_record_count,
                        status = 'completed',
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = NULL
                    WHERE batch_id = :batch_id
                    """
                ),
                {"batch_id": batch_id, "loaded_record_count": loaded_count},
            )
    except Exception as exc:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE raw.load_batches
                    SET status = 'failed', completed_at = NULL, error_message = :error
                    WHERE batch_id = :batch_id
                    """
                ),
                {"batch_id": batch_id, "error": str(exc)[:4000]},
            )
        raise
    return batch_size


def validate_raw_records() -> dict[str, Any]:
    """Mark every ``loaded`` RAW record as ``validated`` or ``rejected``.

    Rejections keep their reasons in ``status_reason``. An encounter already
    validated earlier (or earlier in the same chunk) is rejected as a duplicate,
    so CLEAN never receives the same encounter twice. Afterwards, any batch whose
    rejected share exceeds ``MAX_REJECTED_FRACTION`` fails the task; because the
    statuses are persisted, the gate keeps failing on retries until the data is
    investigated.
    """
    select_loaded = text(
        """
        SELECT raw_record_id, encounter_id, record_data
        FROM raw.records
        WHERE status = 'loaded'
        ORDER BY raw_record_id
        LIMIT :limit
        """
    )
    find_validated = text(
        """
        SELECT encounter_id FROM raw.records
        WHERE status = 'validated' AND encounter_id IN :encounter_ids
        """
    ).bindparams(bindparam("encounter_ids", expanding=True))
    update_status = text(
        """
        UPDATE raw.records
        SET status = :status, status_reason = :status_reason,
            validated_at = CURRENT_TIMESTAMP
        WHERE raw_record_id = :raw_record_id
        """
    )
    reasons: Counter[str] = Counter()
    validated = rejected = 0
    engine = database_engine()
    try:
        while True:
            with engine.begin() as connection:
                rows = connection.execute(
                    select_loaded, {"limit": MAX_BATCH_SIZE}
                ).mappings().all()
                if not rows:
                    break
                seen = set(
                    connection.execute(
                        find_validated,
                        {"encounter_ids": [row["encounter_id"] for row in rows]},
                    ).scalars()
                )
                updates = []
                for row in rows:
                    errors = validate_source_record(row["record_data"])
                    if row["encounter_id"] in seen:
                        errors.append(DUPLICATE_ENCOUNTER)
                    reasons.update(errors)
                    if errors:
                        rejected += 1
                    else:
                        validated += 1
                        seen.add(row["encounter_id"])
                    updates.append(
                        {
                            "raw_record_id": row["raw_record_id"],
                            "status": "rejected" if errors else "validated",
                            "status_reason": "; ".join(errors) or None,
                        }
                    )
                connection.execute(update_status, updates)

        with engine.connect() as connection:
            failing_batches = connection.execute(
                text(
                    """
                    SELECT b.batch_number, b.source_sha256,
                           COUNT(*) AS total_rows,
                           COUNT(*) FILTER (WHERE r.status = 'rejected') AS rejected_rows
                    FROM raw.records r
                    JOIN raw.load_batches b ON b.batch_id = r.batch_id
                    GROUP BY b.batch_number, b.source_sha256
                    HAVING COUNT(*) FILTER (WHERE r.status = 'rejected')
                        > :max_fraction * COUNT(*)
                    """
                ),
                {"max_fraction": MAX_REJECTED_FRACTION},
            ).mappings().all()
            totals = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (WHERE status = 'validated') AS validated,
                           COUNT(*) FILTER (WHERE status = 'rejected') AS rejected
                    FROM raw.records
                    """
                )
            ).mappings().one()
    finally:
        engine.dispose()

    if failing_batches:
        details = ", ".join(
            f"batch {row['batch_number']} ({row['rejected_rows']}/{row['total_rows']})"
            for row in failing_batches
        )
        raise AirflowFailException(
            f"RAW quality gate failed: rejected share above {MAX_REJECTED_FRACTION:.2%} "
            f"in {details}. Inspect raw.records.status_reason."
        )
    return {
        "validated_rows": validated,
        "rejected_rows": rejected,
        "rejection_reasons": dict(reasons),
        "total_validated_rows": totals["validated"],
        "total_rejected_rows": totals["rejected"],
    }
