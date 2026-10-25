"""Data preparation and reproducible baseline training for readmission."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATASET_SCHEMA_VERSION = "diabetes-130-us-v1"
TRANSFORMATION_VERSION = "readmission-features-v1"
TARGET_COLUMN = "readmitted"
PATIENT_COLUMN = "patient_nbr"
ENCOUNTER_COLUMN = "encounter_id"
TARGET_CLASSES = ("NO", ">30", "<30")
CLASS_METRIC_NAMES = {
    "NO": "no_readmission",
    ">30": "readmitted_after_30_days",
    "<30": "readmitted_within_30_days",
}
MISSING_TOKENS = ("", "?", "None")

NUMERIC_FEATURES = (
    "time_in_hospital",
    "num_lab_procedures",
    "num_procedures",
    "num_medications",
    "number_outpatient",
    "number_emergency",
    "number_inpatient",
    "number_diagnoses",
)
FEATURE_COLUMNS = (
    "race",
    "gender",
    "age",
    "admission_type_id",
    "admission_source_id",
    "time_in_hospital",
    "payer_code",
    "medical_specialty",
    "num_lab_procedures",
    "num_procedures",
    "num_medications",
    "number_outpatient",
    "number_emergency",
    "number_inpatient",
    "diag_1",
    "diag_2",
    "diag_3",
    "number_diagnoses",
    "max_glu_serum",
    "A1Cresult",
    "metformin",
    "repaglinide",
    "nateglinide",
    "chlorpropamide",
    "glimepiride",
    "acetohexamide",
    "glipizide",
    "glyburide",
    "tolbutamide",
    "pioglitazone",
    "rosiglitazone",
    "acarbose",
    "miglitol",
    "troglitazone",
    "tolazamide",
    "insulin",
    "glyburide-metformin",
    "glipizide-metformin",
    "glimepiride-pioglitazone",
    "metformin-rosiglitazone",
    "metformin-pioglitazone",
    "change",
    "diabetesMed",
)
EXCLUDED_COLUMNS = frozenset(
    {
        ENCOUNTER_COLUMN,
        PATIENT_COLUMN,
        TARGET_COLUMN,
        "discharge_disposition_id",
        "weight",
        "examide",
        "citoglipton",
    }
)
REQUIRED_COLUMNS = frozenset(
    {
        ENCOUNTER_COLUMN,
        PATIENT_COLUMN,
        TARGET_COLUMN,
        "discharge_disposition_id",
        "weight",
        "examide",
        "citoglipton",
        *FEATURE_COLUMNS,
    }
)
SPLIT_RANDOM_STATE = 42
TEST_FRACTION = 0.15
VALIDATION_FRACTION = 0.15
SPLIT_SEARCH_ATTEMPTS = 256
MAX_BATCH_SIZE = 15_000
LOGISTIC_REGRESSION_C = 0.1
LOGISTIC_REGRESSION_TOL = 1e-3
LOGISTIC_REGRESSION_MAX_ITER = 1000


@dataclass(frozen=True)
class TrainingData:
    """Prepared model inputs and row metadata, before fitting any transforms."""

    features: pd.DataFrame
    target: pd.Series
    patient_ids: pd.Series
    encounter_ids: pd.Series


@dataclass(frozen=True)
class DataSplits:
    """Patient-disjoint row indices for the three model data partitions."""

    train: pd.Index
    validation: pd.Index
    test: pd.Index

    def as_series(self, index: pd.Index) -> pd.Series:
        """Return split labels aligned to the original input index."""
        labels = pd.Series(index=index, dtype="string", name="data_split")
        labels.loc[self.train] = "train"
        labels.loc[self.validation] = "validation"
        labels.loc[self.test] = "test"
        if labels.isna().any():
            raise ValueError("Some rows were not assigned to a data split.")
        return labels


def load_training_data(csv_path: str | Path) -> TrainingData:
    """Read and clean source tokens without fitting data-dependent transforms."""
    frame = pd.read_csv(
        csv_path,
        dtype="string",
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    if frame.empty:
        raise ValueError("The dataset contains no records.")

    missing_columns = REQUIRED_COLUMNS.difference(frame.columns)
    if missing_columns:
        raise ValueError(
            "Dataset is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )
    if frame.columns.duplicated().any():
        raise ValueError("Dataset contains duplicate column names.")

    frame = frame.replace(list(MISSING_TOKENS), pd.NA)
    for column in (ENCOUNTER_COLUMN, PATIENT_COLUMN, TARGET_COLUMN):
        if frame[column].isna().any():
            raise ValueError(f"Required identifier/target column has nulls: {column}")

    if frame[ENCOUNTER_COLUMN].duplicated().any():
        raise ValueError("Dataset contains duplicate encounter_id values.")

    unexpected_targets = set(frame[TARGET_COLUMN].dropna().unique()).difference(
        TARGET_CLASSES
    )
    if unexpected_targets:
        raise ValueError(
            "Dataset contains unsupported readmitted labels: "
            + ", ".join(sorted(unexpected_targets))
        )

    features = frame.loc[:, FEATURE_COLUMNS].copy()
    numeric_features = [column for column in NUMERIC_FEATURES if column in features]
    for column in numeric_features:
        original = features[column]
        converted = pd.to_numeric(original, errors="coerce")
        invalid_values = original.notna() & converted.isna()
        if invalid_values.any():
            examples = ", ".join(original[invalid_values].head(3).astype(str))
            raise ValueError(
                f"Non-numeric values found in numeric feature {column}: {examples}"
            )
        features[column] = converted.astype("float64")

    categorical_features = [
        column for column in features.columns if column not in numeric_features
    ]
    for column in categorical_features:
        values = features[column].astype(object)
        features[column] = values.where(values.notna(), float("nan"))

    return TrainingData(
        features=features,
        target=frame[TARGET_COLUMN].astype("string"),
        patient_ids=frame[PATIENT_COLUMN].astype("string"),
        encounter_ids=frame[ENCOUNTER_COLUMN].astype("string"),
    )


def split_by_patient(
    patient_ids: pd.Series,
    target: pd.Series,
    *,
    random_state: int = SPLIT_RANDOM_STATE,
) -> DataSplits:
    """Create deterministic 70/15/15 splits without patient leakage."""
    if len(patient_ids) != len(target):
        raise ValueError("patient_ids and target must have the same number of rows.")
    if patient_ids.isna().any():
        raise ValueError("Patient identifiers cannot be null when splitting.")
    if target.isna().any():
        raise ValueError("Target values cannot be null when splitting.")
    if patient_ids.nunique() < 3:
        raise ValueError("At least three distinct patients are required for splitting.")

    indices = pd.Index(patient_ids.index)
    train_validation_indices, test_indices = _group_holdout(
        indices,
        patient_ids,
        target,
        test_fraction=TEST_FRACTION,
        random_state=random_state,
    )
    validation_fraction_of_remaining = VALIDATION_FRACTION / (1 - TEST_FRACTION)
    train_indices, validation_indices = _group_holdout(
        train_validation_indices,
        patient_ids,
        target,
        test_fraction=validation_fraction_of_remaining,
        random_state=random_state + SPLIT_SEARCH_ATTEMPTS,
    )

    splits = DataSplits(
        train=train_indices,
        validation=validation_indices,
        test=test_indices,
    )
    split_labels = splits.as_series(indices)
    if set(split_labels.unique()) != {"train", "validation", "test"}:
        raise ValueError("The dataset could not populate all three data splits.")

    for label, split_indices in (
        ("train", splits.train),
        ("validation", splits.validation),
        ("test", splits.test),
    ):
        split_classes = set(target.loc[split_indices].unique())
        if split_classes != set(TARGET_CLASSES):
            raise ValueError(
                f"The {label} split does not contain all target classes: "
                f"{sorted(split_classes)}"
            )

    patient_sets = {
        "train": set(patient_ids.loc[splits.train]),
        "validation": set(patient_ids.loc[splits.validation]),
        "test": set(patient_ids.loc[splits.test]),
    }
    if (
        patient_sets["train"] & patient_sets["validation"]
        or patient_sets["train"] & patient_sets["test"]
        or patient_sets["validation"] & patient_sets["test"]
    ):
        raise ValueError("Patient leakage detected between data splits.")

    return splits


def _group_holdout(
    indices: pd.Index,
    patient_ids: pd.Series,
    target: pd.Series,
    *,
    test_fraction: float,
    random_state: int,
) -> tuple[pd.Index, pd.Index]:
    """Choose a deterministic patient-group split with all classes represented."""
    split_target = target.loc[indices]
    split_groups = patient_ids.loc[indices]
    overall_distribution = split_target.value_counts(normalize=True)
    best_split: tuple[pd.Index, pd.Index] | None = None
    best_score = float("inf")

    for attempt in range(SPLIT_SEARCH_ATTEMPTS):
        splitter = GroupShuffleSplit(
            n_splits=1,
            test_size=test_fraction,
            random_state=random_state + attempt,
        )
        train_positions, test_positions = next(
            splitter.split(indices, split_target, groups=split_groups)
        )
        train_indices = indices.take(train_positions)
        test_indices = indices.take(test_positions)
        if (
            set(target.loc[train_indices].unique()) != set(TARGET_CLASSES)
            or set(target.loc[test_indices].unique()) != set(TARGET_CLASSES)
        ):
            continue

        distribution_error = 0.0
        for partition_indices in (train_indices, test_indices):
            partition_distribution = target.loc[partition_indices].value_counts(
                normalize=True
            )
            distribution_error += sum(
                abs(
                    float(partition_distribution.get(label, 0.0))
                    - float(overall_distribution[label])
                )
                for label in TARGET_CLASSES
            )
        size_error = abs(len(test_indices) / len(indices) - test_fraction)
        score = distribution_error + size_error
        if score < best_score:
            best_score = score
            best_split = (train_indices, test_indices)

    if best_split is None:
        raise ValueError(
            "Could not create patient-disjoint splits containing all target "
            f"classes after {SPLIT_SEARCH_ATTEMPTS} attempts."
        )
    return best_split


def build_pipeline(feature_columns: list[str]) -> Pipeline:
    """Build sparse preprocessing and a class-balanced linear classifier."""
    numeric_features = [
        column for column in NUMERIC_FEATURES if column in feature_columns
    ]
    categorical_features = [
        column for column in feature_columns if column not in numeric_features
    ]
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric_features:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                numeric_features,
            )
        )
    if categorical_features:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    steps=[
                        (
                            "imputer",
                            SimpleImputer(strategy="constant", fill_value="__missing__"),
                        ),
                        (
                            "one_hot",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=True),
                        ),
                    ]
                ),
                categorical_features,
            )
        )

    preprocessor = ColumnTransformer(
        transformers=transformers,
        remainder="drop",
        sparse_threshold=1.0,
        verbose_feature_names_out=False,
    )
    classifier = LogisticRegression(
        class_weight="balanced",
        C=LOGISTIC_REGRESSION_C,
        max_iter=LOGISTIC_REGRESSION_MAX_ITER,
        random_state=SPLIT_RANDOM_STATE,
        solver="lbfgs",
        tol=LOGISTIC_REGRESSION_TOL,
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )


def build_clean_records(
    data: TrainingData,
    splits: DataSplits,
    *,
    batch_size: int = MAX_BATCH_SIZE,
) -> Iterator[pd.DataFrame]:
    """Yield contract-shaped CLEAN batches before resolving raw record IDs."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}.")

    split_labels = splits.as_series(data.features.index)
    feature_rows = data.features.itertuples(index=False, name=None)
    batch_records = []
    for encounter_id, feature_values, target, split in zip(
        data.encounter_ids, feature_rows, data.target, split_labels, strict=True
    ):
        json_features = {}
        for name, value in zip(data.features.columns, feature_values, strict=True):
            if pd.isna(value):
                json_features[name] = None
            elif hasattr(value, "item"):
                json_features[name] = value.item()
            else:
                json_features[name] = value
        batch_records.append(
            {
                "encounter_id": encounter_id,
                "transformation_version": TRANSFORMATION_VERSION,
                "features": json_features,
                "target_readmitted": target,
                "data_split": split,
            }
        )
        if len(batch_records) == batch_size:
            yield pd.DataFrame.from_records(batch_records)
            batch_records = []

    if batch_records:
        yield pd.DataFrame.from_records(batch_records)


def evaluate_predictions(
    target: pd.Series, predictions: Any, *, prefix: str
) -> dict[str, float]:
    """Calculate class-aware metrics, including recall for each class."""
    metrics = {
        f"{prefix}_macro_f1": float(
            f1_score(target, predictions, labels=TARGET_CLASSES, average="macro")
        ),
        f"{prefix}_macro_precision": float(
            precision_score(
                target,
                predictions,
                labels=TARGET_CLASSES,
                average="macro",
                zero_division=0,
            )
        ),
        f"{prefix}_macro_recall": float(
            recall_score(target, predictions, labels=TARGET_CLASSES, average="macro")
        ),
        f"{prefix}_balanced_accuracy": float(
            balanced_accuracy_score(target, predictions)
        ),
    }
    recalls = recall_score(
        target, predictions, labels=TARGET_CLASSES, average=None, zero_division=0
    )
    metrics.update(
        {
            f"{prefix}_recall_{CLASS_METRIC_NAMES[label]}": float(recall)
            for label, recall in zip(TARGET_CLASSES, recalls, strict=True)
        }
    )
    return metrics


def train_and_evaluate(data: TrainingData) -> tuple[Pipeline, DataSplits, dict[str, float]]:
    """Fit on train, report validation metrics, then evaluate once on test."""
    splits = split_by_patient(data.patient_ids, data.target)
    model = build_pipeline(list(data.features.columns))
    model.fit(data.features.loc[splits.train], data.target.loc[splits.train])

    metrics: dict[str, float] = {}
    for split_name, indices in (
        ("validation", splits.validation),
        ("test", splits.test),
    ):
        predictions = model.predict(data.features.loc[indices])
        metrics.update(
            evaluate_predictions(
                data.target.loc[indices], predictions, prefix=split_name
            )
        )

    return model, splits, metrics
