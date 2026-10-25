"""Incrementally ingest, process, train, and promote the diabetes model.

Each run loads the next RAW batch (at most 15,000 rows) of the source file,
validates it, transforms new validated records into CLEAN, assigns patient
splits, and retrains the candidates on all CLEAN data accumulated so far. The
best candidate is registered and replaces the production alias only if it beats
the current champion on the same validation set. When there is no new CLEAN
data, the training branch is skipped.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import pendulum
from airflow.sdk import dag, get_current_context, task


def _dag_run_id() -> str:
    return get_current_context()["run_id"]


@dag(
    dag_id="diabetes_incremental_training",
    description=(
        "Incremental RAW ingestion, CLEAN processing, candidate training in MLflow, "
        "and champion/challenger promotion."
    ),
    schedule=os.environ.get("DIABETES_TRAINING_SCHEDULE", "*/10 * * * *"),
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "mlops",
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["diabetes", "training", "mlflow"],
)
def diabetes_incremental_training() -> None:
    @task
    def validate_source_file() -> dict[str, Any]:
        from diabetes_pipeline.raw import ensure_dataset_available, source_metadata
        from diabetes_pipeline.settings import DATASET_PATH

        ensure_dataset_available(DATASET_PATH)
        return source_metadata(DATASET_PATH)

    @task
    def ingest_next_batch(source: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.raw import ingest_next_batch as ingest

        return ingest(source, _dag_run_id())

    @task
    def validate_raw_quality(batch: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.raw import validate_raw_records

        return {"batch": batch, **validate_raw_records()}

    @task
    def preprocess_raw_to_clean(quality: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.clean import preprocess_to_staging

        return preprocess_to_staging(_dag_run_id())

    @task
    def store_clean_records(staged: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.clean import merge_staging_into_clean

        return {**staged, **merge_staging_into_clean(_dag_run_id())}

    @task
    def split_dataset(stored: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.clean import assign_and_check_splits

        return assign_and_check_splits()

    @task.short_circuit
    def check_training_needed(split: dict[str, Any]) -> bool:
        from diabetes_pipeline.modeling import needs_training

        return needs_training(split["snapshot"])

    @task
    def train_candidates(split: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.modeling import train_and_log_candidates

        return train_and_log_candidates(split["snapshot"], _dag_run_id())

    @task
    def register_best_model(trained: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.modeling import register_best_model as register

        return register(trained)

    @task
    def compare_with_champion(registered: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.modeling import compare_with_champion as compare

        return compare(registered)

    @task
    def promote_model(comparison: dict[str, Any]) -> dict[str, Any]:
        from diabetes_pipeline.modeling import promote_model as promote

        return promote(comparison)

    source = validate_source_file()
    batch = ingest_next_batch(source)
    quality = validate_raw_quality(batch)
    staged = preprocess_raw_to_clean(quality)
    stored = store_clean_records(staged)
    split = split_dataset(stored)
    trained = train_candidates(split)
    check_training_needed(split) >> trained
    promote_model(compare_with_champion(register_best_model(trained)))


diabetes_training_dag = diabetes_incremental_training()
