"""Candidate training, MLflow registration, champion comparison, promotion."""

import time
from pathlib import Path
from typing import Any

import pandas as pd
from airflow.sdk.exceptions import AirflowFailException
from sqlalchemy import text

import diabetes_model
from diabetes_model.contract import (
    DATASET_SCHEMA_VERSION,
    INPUT_COLUMNS,
    NUMERIC_INPUTS,
    TARGET_CLASSES,
    input_frame,
)
from diabetes_model.features import FEATURE_COLUMNS, TRANSFORMATION_VERSION, feature_frame
from diabetes_model.models import (
    CANDIDATES,
    PRIMARY_METRIC,
    build_candidate,
    build_serving_pipeline,
    evaluate,
    evaluate_predictions,
)
from diabetes_model.splits import SPLIT_NAMES, SPLIT_SALT
from diabetes_pipeline.settings import (
    EXPERIMENT_NAME,
    MODEL_ALIAS,
    MODEL_NAME,
    PROMOTION_MIN_IMPROVEMENT,
    database_engine,
    mlflow_tracking_uri,
)

# Types skops may deserialize from our models; anything else is refused.
# HistGradientBoosting stores functools.partial(check_array); skops vets the
# wrapped function separately, so the partial itself is safe to allow.
TRUSTED_TYPE_PREFIXES = ("builtins.", "numpy.", "scipy.", "sklearn.", "diabetes_model.")
TRUSTED_TYPES = frozenset({"functools.partial"})
# Serving the registered artifact must reproduce the training-time score
# (registry tags keep the score with six decimals).
SKEW_TOLERANCE = 1e-6


def _mlflow_client() -> Any:
    import mlflow
    from mlflow.tracking import MlflowClient

    tracking_uri = mlflow_tracking_uri()
    mlflow.set_tracking_uri(tracking_uri)
    return MlflowClient(tracking_uri=tracking_uri)


def needs_training(snapshot: dict[str, Any]) -> bool:
    """Train only for a CLEAN snapshot that is usable and not yet registered."""
    for split in SPLIT_NAMES:
        missing = [
            label for label, rows in snapshot["split_counts"][split].items() if rows == 0
        ]
        if missing:
            print(f"Skipping training: {split} split has no rows for {missing}.")
            return False
    client = _mlflow_client()
    for version in client.search_model_versions(f"name = '{MODEL_NAME}'"):
        if version.tags.get("clean_snapshot") == snapshot["fingerprint"]:
            print(
                f"Skipping training: snapshot {snapshot['fingerprint']} is already "
                f"registered as {MODEL_NAME} version {version.version}."
            )
            return False
    return True


def _load_clean_snapshot(snapshot: dict[str, Any]) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    engine = database_engine()
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT features, target_readmitted, data_split
                    FROM clean.records
                    WHERE transformation_version = :transformation_version
                      AND exclusion_reason IS NULL
                      AND data_split IS NOT NULL
                      AND clean_record_id <= :max_clean_record_id
                    ORDER BY clean_record_id
                    """
                ),
                {
                    "transformation_version": snapshot["transformation_version"],
                    "max_clean_record_id": snapshot["max_clean_record_id"],
                },
            ).mappings().all()
    finally:
        engine.dispose()
    if len(rows) != snapshot["rows"]:
        raise RuntimeError(
            f"CLEAN snapshot changed: expected {snapshot['rows']} rows, read {len(rows)}."
        )
    features = feature_frame(row["features"] for row in rows)
    target = pd.Series([row["target_readmitted"] for row in rows], dtype="string")
    split = pd.Series([row["data_split"] for row in rows], dtype="string")
    return features, target, split


def _load_contract_inputs(
    snapshot: dict[str, Any], data_split: str, limit: int | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Rebuild 44-field model inputs for a split from RAW lineage."""
    engine = database_engine()
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT r.record_data, c.target_readmitted
                    FROM clean.records c
                    JOIN raw.records r ON r.raw_record_id = c.raw_record_id
                    WHERE c.transformation_version = :transformation_version
                      AND c.exclusion_reason IS NULL
                      AND c.data_split = :data_split
                      AND c.clean_record_id <= :max_clean_record_id
                    ORDER BY c.clean_record_id
                    LIMIT :limit
                    """
                ),
                {
                    "transformation_version": snapshot["transformation_version"],
                    "data_split": data_split,
                    "max_clean_record_id": snapshot["max_clean_record_id"],
                    "limit": limit,
                },
            ).mappings().all()
    finally:
        engine.dispose()
    inputs = input_frame(row["record_data"] for row in rows)
    target = pd.Series([row["target_readmitted"] for row in rows], dtype="string")
    return inputs, target


def _model_signature() -> Any:
    from mlflow.models import ModelSignature
    from mlflow.types.schema import ColSpec, Schema

    return ModelSignature(
        inputs=Schema(
            [
                ColSpec("double" if column in NUMERIC_INPUTS else "string", column)
                for column in INPUT_COLUMNS
            ]
        ),
        outputs=Schema([ColSpec("string")]),
    )


def _skops_trusted_types(model: Any) -> list[str]:
    import skops.io

    untrusted = skops.io.get_untrusted_types(data=skops.io.dumps(model))
    unexpected = [
        name
        for name in untrusted
        if name not in TRUSTED_TYPES and not name.startswith(TRUSTED_TYPE_PREFIXES)
    ]
    if unexpected:
        raise RuntimeError(f"Refusing to trust unexpected serialized types: {unexpected}")
    return untrusted


def _log_evaluation_artifacts(
    model: Any, features: pd.DataFrame, target: pd.Series, *, candidate: str, split: str
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mlflow
    from sklearn.metrics import ConfusionMatrixDisplay, classification_report

    predictions = model.predict(features)
    figure, axis = plt.subplots(figsize=(5, 4))
    ConfusionMatrixDisplay.from_predictions(
        target,
        predictions,
        labels=list(TARGET_CLASSES),
        normalize="true",
        values_format=".2f",
        colorbar=False,
        ax=axis,
    )
    axis.set_title(f"{candidate} - {split} (normalized by true class)")
    figure.tight_layout()
    mlflow.log_figure(figure, f"evaluation/confusion_matrix_{split}.png")
    plt.close(figure)
    mlflow.log_dict(
        classification_report(
            target,
            predictions,
            labels=list(TARGET_CLASSES),
            output_dict=True,
            zero_division=0,
        ),
        f"evaluation/classification_report_{split}.json",
    )


def train_and_log_candidates(snapshot: dict[str, Any], dag_run_id: str) -> dict[str, Any]:
    """Fit every candidate on train and log each as a nested MLflow run.

    Each child run records parameters, train/validation/test metrics, confusion
    matrices, classification reports, and the serving pipeline (contract
    inputs -> features -> preprocessing -> classifier) with its signature.
    """
    import mlflow
    import mlflow.sklearn

    _mlflow_client()
    mlflow.set_experiment(EXPERIMENT_NAME)
    features, target, split = _load_clean_snapshot(snapshot)
    masks = {name: split.eq(name).to_numpy() for name in SPLIT_NAMES}
    example_inputs, _ = _load_contract_inputs(snapshot, "train", limit=5)
    signature = _model_signature()
    package_path = str(Path(diabetes_model.__file__).parent)
    common_tags = {
        "dag_run_id": dag_run_id,
        "clean_snapshot": snapshot["fingerprint"],
        "transformation_version": TRANSFORMATION_VERSION,
        "primary_metric": PRIMARY_METRIC,
    }
    common_params = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "transformation_version": TRANSFORMATION_VERSION,
        "clean_snapshot": snapshot["fingerprint"],
        "max_clean_record_id": snapshot["max_clean_record_id"],
        "split_strategy": "sha256(patient_nbr) 70/15/15",
        "split_salt": SPLIT_SALT,
        "feature_count": len(FEATURE_COLUMNS),
        "input_count": len(INPUT_COLUMNS),
        **{f"rows_{name}": int(mask.sum()) for name, mask in masks.items()},
    }

    candidates = []
    with mlflow.start_run(run_name=f"training-{dag_run_id}") as parent:
        mlflow.set_tags({**common_tags, "run_role": "candidate_comparison"})
        mlflow.log_params(common_params)
        mlflow.log_dict(snapshot, "data/clean_snapshot.json")
        for name in CANDIDATES:
            with mlflow.start_run(run_name=name, nested=True) as child:
                started = time.perf_counter()
                model = build_candidate(name).fit(
                    features[masks["train"]], target[masks["train"]]
                )
                fit_seconds = time.perf_counter() - started
                metrics = {"fit_seconds": fit_seconds}
                for split_name, mask in masks.items():
                    metrics.update(
                        evaluate(model, features[mask], target[mask], prefix=split_name)
                    )
                mlflow.set_tags({**common_tags, "run_role": "candidate", "candidate": name})
                mlflow.log_params(
                    {
                        **common_params,
                        "candidate": name,
                        "classifier": type(model.named_steps["classifier"]).__name__,
                        **{
                            f"classifier.{key}": value
                            for key, value in model.named_steps["classifier"]
                            .get_params()
                            .items()
                            if key != "categorical_features"
                        },
                    }
                )
                mlflow.log_metrics(metrics)
                for split_name in ("validation", "test"):
                    _log_evaluation_artifacts(
                        model,
                        features[masks[split_name]],
                        target[masks[split_name]],
                        candidate=name,
                        split=split_name,
                    )
                serving_model = build_serving_pipeline(model)
                model_info = mlflow.sklearn.log_model(
                    sk_model=serving_model,
                    name="model",
                    signature=signature,
                    input_example=example_inputs,
                    code_paths=[package_path],
                    skops_trusted_types=_skops_trusted_types(serving_model),
                )
                candidates.append(
                    {
                        "candidate": name,
                        "run_id": child.info.run_id,
                        "model_uri": model_info.model_uri,
                        "validation_macro_f1": metrics[PRIMARY_METRIC],
                        "test_macro_f1": metrics["test_macro_f1"],
                    }
                )
                print(f"{name}: {PRIMARY_METRIC}={metrics[PRIMARY_METRIC]:.4f}")

        best = max(candidates, key=lambda candidate: candidate["validation_macro_f1"])
        mlflow.log_dict({"candidates": candidates}, "candidates_comparison.json")
        mlflow.log_metric("best_validation_macro_f1", best["validation_macro_f1"])
        mlflow.set_tag("best_candidate", best["candidate"])
    return {
        "parent_run_id": parent.info.run_id,
        "snapshot": snapshot,
        "candidates": candidates,
        "best": best,
    }


def register_best_model(trained: dict[str, Any]) -> dict[str, Any]:
    """Register the best candidate once per CLEAN snapshot (retry-safe)."""
    import mlflow

    client = _mlflow_client()
    snapshot = trained["snapshot"]
    best = trained["best"]
    existing = [
        version
        for version in client.search_model_versions(f"name = '{MODEL_NAME}'")
        if version.tags.get("clean_snapshot") == snapshot["fingerprint"]
    ]
    if existing:
        version = max(existing, key=lambda item: int(item.version))
    else:
        version = mlflow.register_model(
            best["model_uri"],
            MODEL_NAME,
            tags={
                "clean_snapshot": snapshot["fingerprint"],
                "candidate": best["candidate"],
                "transformation_version": TRANSFORMATION_VERSION,
                "validation_macro_f1": f"{best['validation_macro_f1']:.6f}",
                "test_macro_f1": f"{best['test_macro_f1']:.6f}",
                "training_run_id": best["run_id"],
            },
        )
        client.update_model_version(
            MODEL_NAME,
            version.version,
            description=(
                f"{best['candidate']} trained on CLEAN snapshot {snapshot['fingerprint']} "
                f"({snapshot['rows']} rows, {TRANSFORMATION_VERSION}). "
                f"Validation macro-F1 {best['validation_macro_f1']:.4f}."
            ),
        )
    client.set_tag(trained["parent_run_id"], "registered_model_version", version.version)
    return {
        "model_name": MODEL_NAME,
        "version": version.version,
        "candidate": version.tags.get("candidate", best["candidate"]),
        "validation_macro_f1": float(
            version.tags.get("validation_macro_f1", best["validation_macro_f1"])
        ),
        "parent_run_id": trained["parent_run_id"],
        "snapshot": snapshot,
    }


def compare_with_champion(registered: dict[str, Any]) -> dict[str, Any]:
    """Score the new version and the current champion on the same validation set.

    Both models are loaded from the registry and fed contract inputs rebuilt
    from RAW, exactly as the API will call them. The new version's score must
    also match its training-time score, which guards against training/serving
    skew in the logged artifact.
    """
    import mlflow

    client = _mlflow_client()
    inputs, target = _load_contract_inputs(registered["snapshot"], "validation")
    candidate = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{registered['version']}")
    candidate_score = evaluate_predictions(
        target, candidate.predict(inputs), prefix="validation"
    )[PRIMARY_METRIC]
    if abs(candidate_score - registered["validation_macro_f1"]) > SKEW_TOLERANCE:
        raise AirflowFailException(
            f"Serving/training skew: registered version {registered['version']} scores "
            f"{candidate_score:.6f} from the registry but {registered['validation_macro_f1']:.6f} "
            "at training time."
        )

    # Read aliases from the registered model: a missing alias is reported with
    # different error codes across MLflow versions.
    champion_version = client.get_registered_model(MODEL_NAME).aliases.get(MODEL_ALIAS)
    champion = (
        None
        if champion_version is None
        else client.get_model_version(MODEL_NAME, champion_version)
    )

    champion_score = None
    if champion is None:
        promote, reason = True, "no_current_champion"
    elif champion.version == registered["version"]:
        promote, reason = True, "already_champion"
        champion_score = candidate_score
    else:
        champion_model = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{champion.version}")
        schema = champion_model.metadata.get_input_schema()
        champion_inputs = inputs[schema.input_names()] if schema else inputs
        champion_score = evaluate_predictions(
            target, champion_model.predict(champion_inputs), prefix="validation"
        )[PRIMARY_METRIC]
        promote = candidate_score > champion_score + PROMOTION_MIN_IMPROVEMENT
        reason = "better_than_champion" if promote else "not_better_than_champion"

    client.log_metric(registered["parent_run_id"], "candidate_registry_validation_macro_f1", candidate_score)
    if champion_score is not None:
        client.log_metric(registered["parent_run_id"], "champion_validation_macro_f1", champion_score)
    print(
        f"Candidate v{registered['version']}={candidate_score:.4f}; "
        f"champion={'none' if champion is None else 'v' + champion.version}"
        f"={champion_score}; decision={reason}"
    )
    return {
        **registered,
        "validation_rows": len(target),
        "candidate_validation_macro_f1": candidate_score,
        "champion_version": None if champion is None else champion.version,
        "champion_validation_macro_f1": champion_score,
        "promote": promote,
        "reason": reason,
    }


def promote_model(comparison: dict[str, Any]) -> dict[str, Any]:
    """Move the production alias to the new version when it wins."""
    client = _mlflow_client()
    version = comparison["version"]
    client.set_model_version_tag(
        MODEL_NAME, version, "promotion_decision", comparison["reason"]
    )
    client.set_model_version_tag(
        MODEL_NAME,
        version,
        "compared_with_version",
        comparison["champion_version"] or "none",
    )
    if comparison["promote"]:
        client.set_registered_model_alias(MODEL_NAME, MODEL_ALIAS, version)
    client.log_metric(
        comparison["parent_run_id"], "promoted_to_production", float(comparison["promote"])
    )
    current = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    return {
        "model_name": MODEL_NAME,
        "model_alias": MODEL_ALIAS,
        "evaluated_version": version,
        "promoted": comparison["promote"],
        "reason": comparison["reason"],
        "production_version": current.version,
        "candidate_validation_macro_f1": comparison["candidate_validation_macro_f1"],
        "champion_validation_macro_f1": comparison["champion_validation_macro_f1"],
    }
