"""Runtime configuration read from the deployment environment."""

import os
from pathlib import Path
from typing import Any

DATASET_PATH = Path(
    os.environ.get("DIABETES_DATASET_PATH", "/opt/airflow/data/Diabetes/Diabetes.csv")
)
DATASET_URL = os.environ.get(
    "DIABETES_SOURCE_URI",
    "https://docs.google.com/uc?export=download&confirm=t"
    "&id=1k5-1caezQ3zWJbKaiMULTGq-3sz6uThC",
)
MAX_BATCH_SIZE = 15_000
# A batch whose rejected share exceeds this fraction stops the pipeline.
MAX_REJECTED_FRACTION = float(os.environ.get("RAW_MAX_REJECTED_FRACTION", "0.01"))

MODEL_NAME = os.environ.get("MODEL_NAME", "diabetes")
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "production")
EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "diabetes-readmission")
# A candidate must beat the champion's validation macro-F1 by more than this.
PROMOTION_MIN_IMPROVEMENT = float(os.environ.get("PROMOTION_MIN_IMPROVEMENT", "0.0"))


def database_engine() -> Any:
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


def mlflow_tracking_uri() -> str:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RuntimeError("MLFLOW_TRACKING_URI must be configured.")
    return tracking_uri
