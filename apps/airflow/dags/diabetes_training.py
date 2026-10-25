"""Incrementally ingest, process, train, and promote the diabetes model."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import dag, task
from airflow.sdk.exceptions import AirflowFailException

DATASET_PATH = Path(
    os.environ.get(
        "DIABETES_DATASET_PATH",
        "/opt/airflow/data/Diabetes/Diabetes.csv",
    )
)
DATASET_URL = os.environ.get(
    "DIABETES_SOURCE_URI",
    "https://docs.google.com/uc?export=download&confirm=t"
    "&id=1k5-1caezQ3zWJbKaiMULTGq-3sz6uThC",
)
MAX_BATCH_SIZE = 15_000
BATCH_NAMESPACE = uuid.UUID("81ef2d31-3813-4c32-a9c2-310b3bc590ef")
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "production")
MODEL_NAME = os.environ.get("MODEL_NAME", "diabetes")
TRANSFORMATION_VERSION = "readmission-features-v1"
TARGET_CLASSES = ("NO", ">30", "<30")


def _database_engine() -> Any:
    """Create a SQLAlchemy engine from the training DB environment."""
    from sqlalchemy import URL, create_engine

    database_url = os.environ.get("TRAIN_DATABASE_URL")
    if database_url:
        return create_engine(database_url, pool_pre_ping=True)

    required = ("TRAIN_DB_HOST", "TRAIN_DB_NAME", "TRAIN_DB_USER", "TRAIN_DB_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Training database configuration is missing: " + ", ".join(missing)
        )

    return create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.environ["TRAIN_DB_USER"],
            password=os.environ["TRAIN_DB_PASSWORD"],
            host=os.environ["TRAIN_DB_HOST"],
            port=int(os.environ.get("TRAIN_DB_PORT", "5432")),
            database=os.environ["TRAIN_DB_NAME"],
        ),
        pool_pre_ping=True,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_metadata(path: Path) -> dict[str, Any]:
    """Validate source schema, keys, labels, and record count."""
    from diabetes_model.pipeline import (
        ENCOUNTER_COLUMN,
        EXCLUDED_COLUMNS,
        FEATURE_COLUMNS,
        PATIENT_COLUMN,
        TARGET_COLUMN,
    )

    if not path.is_file():
        raise AirflowFailException(f"Dataset file is unavailable: {path}")
    if path.stat().st_size == 0:
        raise AirflowFailException(f"Dataset file is empty: {path}")

    encounter_ids: set[str] = set()
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise AirflowFailException("Dataset CSV has no header.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise AirflowFailException("Dataset CSV has duplicate column names.")

        expected_columns = set(FEATURE_COLUMNS) | set(EXCLUDED_COLUMNS)
        missing_columns = expected_columns.difference(reader.fieldnames)
        extra_columns = set(reader.fieldnames).difference(expected_columns)
        if missing_columns or extra_columns:
            raise AirflowFailException(
                "Dataset schema mismatch. "
                f"Missing columns: {sorted(missing_columns)}; "
                f"unexpected columns: {sorted(extra_columns)}"
            )

        for row_number, row in enumerate(reader, start=1):
            if None in row:
                raise AirflowFailException(
                    f"CSV row {row_number} contains more values than its header."
                )
            if any(value is None for value in row.values()):
                raise AirflowFailException(
                    f"CSV row {row_number} contains fewer values than its header."
                )
            encounter_id = row[ENCOUNTER_COLUMN]
            patient_id = row[PATIENT_COLUMN]
            target = row[TARGET_COLUMN]
            if not encounter_id or not patient_id:
                raise AirflowFailException(
                    f"CSV row {row_number} has an empty encounter or patient ID."
                )
            if encounter_id in encounter_ids:
                raise AirflowFailException(
                    f"Duplicate encounter_id {encounter_id!r} in CSV row {row_number}."
                )
            if target not in TARGET_CLASSES:
                raise AirflowFailException(
                    f"Unsupported readmitted value {target!r} in CSV row {row_number}."
                )
            encounter_ids.add(encounter_id)
            row_count += 1

    if row_count == 0:
        raise AirflowFailException("Dataset CSV contains no data rows.")

    return {
        "path": str(path),
        "source_file": path.name,
        "source_uri": DATASET_URL,
        "source_sha256": _sha256_file(path),
        "row_count": row_count,
        "columns": len(expected_columns),
    }


def _ensure_dataset_available(path: Path) -> None:
    """Download the workshop source into its persistent mount when absent."""
    if path.is_file():
        return
    from scripts.download_diabetes_dataset import download_dataset

    download_dataset(destination=path, url=DATASET_URL)


def _hash_record(record: dict[str, str]) -> str:
    canonical_record = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()


def _insert_raw_batch(
    engine: Any,
    *,
    source: dict[str, Any],
    batch_number: int,
    first_row_number: int,
    rows: list[dict[str, str]],
) -> int:
    """Persist or resume one atomic, idempotent raw batch."""
    from sqlalchemy import text

    batch_id = uuid.uuid5(
        BATCH_NAMESPACE, f"{source['source_sha256']}:{batch_number}"
    )
    batch_size = len(rows)
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"Invalid raw batch size: {batch_size}")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO raw.load_batches (
                    batch_id, source_file, source_uri, source_sha256,
                    batch_number, expected_record_count, status
                )
                VALUES (
                    :batch_id, :source_file, :source_uri, :source_sha256,
                    :batch_number, :expected_record_count, 'loading'
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
            {
                "source_sha256": source["source_sha256"],
                "batch_number": batch_number,
            },
        ).mappings().one()
        if (
            batch["batch_id"] != batch_id
            or batch["expected_record_count"] != batch_size
        ):
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
                SET status = 'loading', completed_at = NULL, error_message = NULL
                WHERE batch_id = :batch_id
                """
            ),
            {"batch_id": batch_id},
        )

    record_values = [
        {
            "batch_id": batch_id,
            "source_sha256": source["source_sha256"],
            "source_row_number": first_row_number + offset,
            "source_record_sha256": _hash_record(record),
            "encounter_id": record["encounter_id"],
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
                      AND source_row_number BETWEEN :first_row_number
                          AND :last_row_number
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


@dag(
    dag_id="diabetes_incremental_training",
    description=(
        "Idempotent raw ingestion, data preparation, MLflow experiment comparison, "
        "and production model promotion."
    ),
    schedule="@weekly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "mlops",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["diabetes", "training", "mlflow"],
)
def diabetes_incremental_training() -> None:
    @task
    def validate_source_file() -> dict[str, Any]:
        _ensure_dataset_available(DATASET_PATH)
        return {
            **_source_metadata(DATASET_PATH),
            "airflow_run_id": os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", "manual"),
        }

    @task
    def ingest_raw_in_batches(source: dict[str, Any]) -> dict[str, Any]:
        engine = _database_engine()
        inserted_rows = 0
        batch_count = 0
        try:
            with Path(source["path"]).open(
                "r", encoding="utf-8-sig", newline=""
            ) as dataset:
                reader = csv.DictReader(dataset)
                batch: list[dict[str, str]] = []
                first_row_number = 1
                for row_number, row in enumerate(reader, start=1):
                    batch.append(row)
                    if len(batch) == MAX_BATCH_SIZE:
                        inserted_rows += _insert_raw_batch(
                            engine,
                            source=source,
                            batch_number=batch_count + 1,
                            first_row_number=first_row_number,
                            rows=batch,
                        )
                        batch_count += 1
                        first_row_number = row_number + 1
                        batch = []
                if batch:
                    inserted_rows += _insert_raw_batch(
                        engine,
                        source=source,
                        batch_number=batch_count + 1,
                        first_row_number=first_row_number,
                        rows=batch,
                    )
                    batch_count += 1
        finally:
            engine.dispose()

        if batch_count != (source["row_count"] + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE:
            raise RuntimeError(
                f"Expected {(source['row_count'] + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE} "
                f"batches, read {batch_count}."
            )
        return {
            **source,
            "batch_count": batch_count,
            "newly_inserted_rows": inserted_rows,
        }

    @task
    def validate_raw_quality(source: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text

        engine = _database_engine()
        try:
            with engine.connect() as connection:
                counts = connection.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS total_rows,
                            COUNT(DISTINCT encounter_id) AS distinct_encounters,
                            COUNT(*) FILTER (WHERE status <> 'loaded') AS non_loaded_rows
                        FROM raw.records
                        WHERE source_sha256 = :source_sha256
                        """
                    ),
                    {"source_sha256": source["source_sha256"]},
                ).mappings().one()
                incomplete_batches = connection.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM raw.load_batches
                        WHERE source_sha256 = :source_sha256
                          AND status <> 'completed'
                        """
                    ),
                    {"source_sha256": source["source_sha256"]},
                ).scalar_one()
        finally:
            engine.dispose()

        if counts["total_rows"] != source["row_count"]:
            raise AirflowFailException(
                f"RAW contains {counts['total_rows']} rows; "
                f"source contains {source['row_count']}."
            )
        if counts["distinct_encounters"] != source["row_count"]:
            raise AirflowFailException(
                "RAW contains duplicate encounter IDs for this source snapshot."
            )
        if counts["non_loaded_rows"] or incomplete_batches:
            raise AirflowFailException(
                "RAW quality check failed: records or batches are not complete."
            )
        return source

    @task
    def process_and_store_clean(source: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import bindparam, text

        from diabetes_model.pipeline import (
            TRANSFORMATION_VERSION as PIPELINE_TRANSFORMATION_VERSION,
            build_clean_records,
            load_training_data,
            split_by_patient,
        )

        if PIPELINE_TRANSFORMATION_VERSION != TRANSFORMATION_VERSION:
            raise RuntimeError("DAG and shared pipeline transformation versions differ.")
        engine = _database_engine()
        try:
            training_data = load_training_data(source["path"])
            splits = split_by_patient(
                training_data.patient_ids, training_data.target
            )
            split_sizes = {
                "train": len(splits.train),
                "validation": len(splits.validation),
                "test": len(splits.test),
            }
            batch_count = 0
            processed_rows = 0
            lookup = text(
                """
                SELECT raw_record_id, encounter_id
                FROM raw.records
                WHERE source_sha256 = :source_sha256
                  AND encounter_id IN :encounter_ids
                """
            ).bindparams(bindparam("encounter_ids", expanding=True))
            upsert_clean = text(
                """
                INSERT INTO clean.records (
                    raw_record_id, transformation_version, features,
                    target_readmitted, data_split
                )
                VALUES (
                    :raw_record_id, :transformation_version,
                    CAST(:features AS jsonb), :target_readmitted, :data_split
                )
                ON CONFLICT (raw_record_id, transformation_version)
                DO UPDATE SET
                    features = EXCLUDED.features,
                    target_readmitted = EXCLUDED.target_readmitted,
                    data_split = EXCLUDED.data_split,
                    processed_at = CURRENT_TIMESTAMP
                """
            )
            for clean_batch in build_clean_records(training_data, splits):
                batch_records = clean_batch.to_dict(orient="records")
                encounter_ids = [record["encounter_id"] for record in batch_records]
                with engine.begin() as connection:
                    raw_ids = {
                        row["encounter_id"]: row["raw_record_id"]
                        for row in connection.execute(
                            lookup,
                            {
                                "source_sha256": source["source_sha256"],
                                "encounter_ids": encounter_ids,
                            },
                        ).mappings()
                    }
                    if len(raw_ids) != len(encounter_ids):
                        raise RuntimeError(
                            "CLEAN batch contains encounters missing from RAW."
                        )
                    values = [
                        {
                            "raw_record_id": raw_ids[record["encounter_id"]],
                            "transformation_version": record[
                                "transformation_version"
                            ],
                            "features": json.dumps(
                                record["features"], allow_nan=False
                            ),
                            "target_readmitted": record["target_readmitted"],
                            "data_split": record["data_split"],
                        }
                        for record in batch_records
                    ]
                    connection.execute(upsert_clean, values)
                batch_count += 1
                processed_rows += len(batch_records)

            if processed_rows != source["row_count"]:
                raise RuntimeError(
                    f"CLEAN processed {processed_rows} rows; "
                    f"source has {source['row_count']}."
                )
            with engine.connect() as connection:
                clean_count = connection.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM clean.records c
                        JOIN raw.records r ON r.raw_record_id = c.raw_record_id
                        WHERE r.source_sha256 = :source_sha256
                          AND c.transformation_version = :transformation_version
                        """
                    ),
                    {
                        "source_sha256": source["source_sha256"],
                        "transformation_version": TRANSFORMATION_VERSION,
                    },
                ).scalar_one()
            if clean_count != source["row_count"]:
                raise RuntimeError(
                    f"CLEAN has {clean_count} records after processing; "
                    f"expected {source['row_count']}."
                )
        finally:
            engine.dispose()

        return {
            **source,
            "transformation_version": TRANSFORMATION_VERSION,
            "clean_rows": processed_rows,
            "clean_batches": batch_count,
            "split_sizes": split_sizes,
        }

    @task
    def train_compare_and_promote(source: dict[str, Any]) -> dict[str, Any]:
        import mlflow
        import mlflow.sklearn
        import pandas as pd
        from mlflow.exceptions import MlflowException
        from mlflow.models.signature import infer_signature
        from mlflow.tracking import MlflowClient
        from sqlalchemy import text

        from diabetes_model.pipeline import (
            FEATURE_COLUMNS,
            build_pipeline,
            evaluate_predictions,
        )

        tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
        if not tracking_uri:
            raise RuntimeError("MLFLOW_TRACKING_URI must be configured.")
        mlflow.set_tracking_uri(tracking_uri)
        experiment_name = os.environ.get(
            "MLFLOW_EXPERIMENT_NAME", "diabetes-readmission"
        )
        experiment = mlflow.set_experiment(experiment_name)
        client = MlflowClient(tracking_uri=tracking_uri)

        engine = _database_engine()
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        """
                        SELECT c.features, c.target_readmitted, c.data_split
                        FROM clean.records c
                        JOIN raw.records r ON r.raw_record_id = c.raw_record_id
                        WHERE r.source_sha256 = :source_sha256
                          AND c.transformation_version = :transformation_version
                        ORDER BY r.source_row_number
                        """
                    ),
                    {
                        "source_sha256": source["source_sha256"],
                        "transformation_version": TRANSFORMATION_VERSION,
                    },
                ).mappings().all()
        finally:
            engine.dispose()
        if len(rows) != source["row_count"]:
            raise RuntimeError(
                f"Training read {len(rows)} CLEAN records; expected {source['row_count']}."
            )

        features = pd.DataFrame(
            [dict(row["features"]) for row in rows],
            columns=FEATURE_COLUMNS,
        )
        target = pd.Series(
            [row["target_readmitted"] for row in rows], dtype="string"
        )
        split = pd.Series([row["data_split"] for row in rows], dtype="string")
        train_mask = split.eq("train")
        validation_mask = split.eq("validation")
        test_mask = split.eq("test")
        if not train_mask.any() or not validation_mask.any() or not test_mask.any():
            raise RuntimeError("CLEAN must contain train, validation, and test rows.")

        training_job_id = hashlib.sha256(
            (
                f"{source['airflow_run_id']}:{source['source_sha256']}:"
                f"{TRANSFORMATION_VERSION}"
            ).encode("utf-8")
        ).hexdigest()
        existing_runs = client.search_runs(
            experiment_ids=[experiment.experiment_id],
            filter_string=f"tags.training_job_id = '{training_job_id}'",
            order_by=["attributes.start_time DESC"],
            max_results=10,
        )
        registered_model = None
        run_id: str | None = None
        metrics: dict[str, float] = {}
        model = None
        for existing_run in existing_runs:
            if "validation_macro_f1" not in existing_run.data.metrics:
                continue
            versions = client.search_model_versions(
                filter_string=f"name = '{MODEL_NAME}'"
            )
            matching_versions = [
                version
                for version in versions
                if version.run_id == existing_run.info.run_id
            ]
            if len(matching_versions) == 1:
                registered_model = matching_versions[0]
                run_id = existing_run.info.run_id
                metrics = dict(existing_run.data.metrics)
                break

        if registered_model is None:
            model = build_pipeline(list(FEATURE_COLUMNS))
            model.fit(features.loc[train_mask], target.loc[train_mask])
            validation_metrics = evaluate_predictions(
                target.loc[validation_mask],
                model.predict(features.loc[validation_mask]),
                prefix="validation",
            )
            test_metrics = evaluate_predictions(
                target.loc[test_mask],
                model.predict(features.loc[test_mask]),
                prefix="test",
            )
            metrics = {**validation_metrics, **test_metrics}

        previous_score: float | None = None
        current_alias_version: str | None = None
        try:
            current_alias = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_DOES_NOT_EXIST":
                raise
        else:
            current_alias_version = current_alias.version
            champion = mlflow.pyfunc.load_model(
                f"models:/{MODEL_NAME}@{MODEL_ALIAS}"
            )
            previous_predictions = champion.predict(
                features.loc[validation_mask]
            )
            previous_score = evaluate_predictions(
                target.loc[validation_mask],
                previous_predictions,
                prefix="previous_validation",
            )["previous_validation_macro_f1"]
            metrics["previous_champion_validation_macro_f1"] = previous_score

        if registered_model is None:
            if model is None:
                raise RuntimeError("A new model run requires a fitted pipeline.")
            with mlflow.start_run(
                run_name=f"diabetes-{source['source_sha256'][:12]}"
            ) as run:
                mlflow.log_params(
                    {
                        "dataset_schema_version": "diabetes-130-us-v1",
                        "transformation_version": TRANSFORMATION_VERSION,
                        "source_sha256": source["source_sha256"],
                        "source_rows": source["row_count"],
                        "feature_count": len(FEATURE_COLUMNS),
                        "model_type": "class_weighted_logistic_regression",
                        "primary_selection_metric": "validation_macro_f1",
                        "split_random_state": 42,
                        "training_job_id": training_job_id,
                    }
                )
                mlflow.log_params(
                    {
                        f"classifier_{key}": value
                        for key, value in model.named_steps[
                            "classifier"
                        ].get_params().items()
                    }
                )
                mlflow.log_metrics(metrics)
                mlflow.log_dict(
                    {
                        "dataset_sha256": source["source_sha256"],
                        "transformation_version": TRANSFORMATION_VERSION,
                        "split_sizes": source["split_sizes"],
                        "metrics": metrics,
                    },
                    "training_summary.json",
                )
                mlflow.set_tags(
                    {
                        "dataset_sha256": source["source_sha256"],
                        "transformation_version": TRANSFORMATION_VERSION,
                        "primary_metric": "validation_macro_f1",
                        "training_job_id": training_job_id,
                    }
                )
                signature = infer_signature(
                    features.loc[train_mask].head(5),
                    model.predict(features.loc[train_mask].head(5)),
                )
                model_info = mlflow.sklearn.log_model(
                    sk_model=model,
                    name="diabetes_model",
                    signature=signature,
                    input_example=features.loc[train_mask].head(5),
                    registered_model_name=MODEL_NAME,
                    skops_trusted_types=["numpy.dtype"],
                )
                versions = client.search_model_versions(
                    filter_string=f"name = '{MODEL_NAME}'"
                )
                matching_versions = [
                    version
                    for version in versions
                    if version.run_id == run.info.run_id
                ]
                if len(matching_versions) != 1:
                    raise RuntimeError(
                        "Could not identify exactly one registered MLflow model "
                        f"version for training run {run.info.run_id} "
                        f"(model URI {model_info.model_uri})."
                    )
                registered_model = matching_versions[0]
                run_id = run.info.run_id

        if registered_model is None or run_id is None:
            raise RuntimeError("Training did not produce a registered model version.")
        selection_score = metrics["validation_macro_f1"]
        promote = (
            previous_score is None
            or selection_score > previous_score
            or current_alias_version == registered_model.version
        )
        if promote:
            client.set_registered_model_alias(
                MODEL_NAME,
                MODEL_ALIAS,
                registered_model.version,
            )
        client.log_metric(
            run_id,
            "promoted_to_production",
            1.0 if promote else 0.0,
        )
        return {
            "model_name": MODEL_NAME,
            "model_version": registered_model.version,
            "model_alias": MODEL_ALIAS,
            "promoted": promote,
            "validation_macro_f1": selection_score,
            "previous_champion_validation_macro_f1": previous_score,
            "run_id": run_id,
            "metrics": metrics,
        }

    source = validate_source_file()
    raw = ingest_raw_in_batches(source)
    quality_checked = validate_raw_quality(raw)
    clean = process_and_store_clean(quality_checked)
    train_compare_and_promote(clean)


diabetes_training_dag = diabetes_incremental_training()
