"""Run the shared training pipeline on a local CSV file."""

import argparse
import json
from pathlib import Path

from diabetes_model.pipeline import (
    DATASET_SCHEMA_VERSION,
    TRANSFORMATION_VERSION,
    load_training_data,
    train_and_evaluate,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Path to Diabetes.csv")
    args = parser.parse_args()

    data = load_training_data(args.dataset)
    model, splits, metrics = train_and_evaluate(data)
    result = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "transformation_version": TRANSFORMATION_VERSION,
        "rows": len(data.target),
        "feature_count": len(data.features.columns),
        "split_rows": {
            "train": len(splits.train),
            "validation": len(splits.validation),
            "test": len(splits.test),
        },
        "primary_selection_metric": "validation_macro_f1",
        "validation_macro_f1": metrics["validation_macro_f1"],
        "metrics": metrics,
        "classes": list(model.named_steps["classifier"].classes_),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
