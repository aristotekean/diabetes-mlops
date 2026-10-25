"""Tests for the data contract and shared training pipeline."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sklearn.pipeline import Pipeline

from diabetes_model.pipeline import (
    EXCLUDED_COLUMNS,
    FEATURE_COLUMNS,
    TARGET_CLASSES,
    build_clean_records,
    evaluate_predictions,
    load_training_data,
    split_by_patient,
)


def make_dataset() -> pd.DataFrame:
    rows = []
    for patient_number in range(30):
        for encounter_number in range(2):
            label = TARGET_CLASSES[patient_number % len(TARGET_CLASSES)]
            row = {
                "encounter_id": str(patient_number * 10 + encounter_number),
                "patient_nbr": str(patient_number),
                "readmitted": label,
                "time_in_hospital": str(patient_number + encounter_number),
                "num_lab_procedures": "?" if encounter_number else "20",
                "num_procedures": "2",
                "num_medications": "5",
                "number_outpatient": "0",
                "number_emergency": "0",
                "number_inpatient": "1",
                "number_diagnoses": "3",
                "discharge_disposition_id": "1",
                "weight": "?",
                "examide": "No",
                "citoglipton": "No",
                "gender": "Female" if patient_number % 2 else "Male",
                "medical_specialty": "?",
            }
            for column in FEATURE_COLUMNS:
                row.setdefault(column, "No")
            rows.append(row)
    return pd.DataFrame(rows)


class TrainingPipelineTests(unittest.TestCase):
    def test_load_converts_missing_tokens_and_excludes_ids_and_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diabetes.csv"
            make_dataset().to_csv(path, index=False)

            data = load_training_data(path)

        self.assertEqual(len(data.features), 60)
        self.assertTrue(EXCLUDED_COLUMNS.isdisjoint(data.features.columns))
        self.assertTrue(pd.isna(data.features.loc[1, "num_lab_procedures"]))
        self.assertTrue(pd.isna(data.features.loc[1, "medical_specialty"]))
        self.assertEqual(data.target.unique().tolist(), list(TARGET_CLASSES))

    def test_splits_keep_each_patient_in_one_partition(self) -> None:
        frame = make_dataset()
        patients = frame["patient_nbr"].astype("string")
        target = frame["readmitted"].astype("string")

        splits = split_by_patient(patients, target)

        patient_sets = [
            set(patients.loc[indices])
            for indices in (splits.train, splits.validation, splits.test)
        ]
        self.assertTrue(patient_sets[0].isdisjoint(patient_sets[1]))
        self.assertTrue(patient_sets[0].isdisjoint(patient_sets[2]))
        self.assertTrue(patient_sets[1].isdisjoint(patient_sets[2]))
        self.assertEqual(len(splits.train) + len(splits.validation) + len(splits.test), len(frame))

    def test_contract_rejects_duplicate_encounters(self) -> None:
        frame = make_dataset()
        frame.loc[1, "encounter_id"] = frame.loc[0, "encounter_id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diabetes.csv"
            frame.to_csv(path, index=False)

            with self.assertRaisesRegex(ValueError, "duplicate encounter_id"):
                load_training_data(path)

    def test_clean_records_match_contract_and_are_json_serializable(self) -> None:
        frame = make_dataset()
        frame["num_lab_procedures"] = "?"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diabetes.csv"
            frame.to_csv(path, index=False)
            data = load_training_data(path)

        splits = split_by_patient(data.patient_ids, data.target)
        batches = list(build_clean_records(data, splits, batch_size=17))
        clean_records = pd.concat(batches, ignore_index=True)

        self.assertEqual(
            list(clean_records.columns),
            [
                "encounter_id",
                "transformation_version",
                "features",
                "target_readmitted",
                "data_split",
            ],
        )
        self.assertEqual(len(clean_records), len(frame))
        self.assertTrue(all(len(batch) <= 17 for batch in batches))
        self.assertEqual([len(batch) for batch in batches], [17, 17, 17, 9])
        self.assertTrue(EXCLUDED_COLUMNS.isdisjoint(clean_records.iloc[0]["features"]))
        self.assertIsNone(
            clean_records.iloc[0]["features"]["num_lab_procedures"]
        )
        json.dumps(clean_records.iloc[0]["features"], allow_nan=False)

    def test_per_class_metric_names_are_mlflow_compatible(self) -> None:
        metrics = evaluate_predictions(
            pd.Series(TARGET_CLASSES),
            TARGET_CLASSES,
            prefix="validation",
        )

        self.assertIn("validation_recall_no_readmission", metrics)
        self.assertIn("validation_recall_readmitted_after_30_days", metrics)
        self.assertIn("validation_recall_readmitted_within_30_days", metrics)
        self.assertTrue(
            all(
                all(character.isalnum() or character == "_" for character in name)
                for name in metrics
            )
        )


if __name__ == "__main__":
    unittest.main()
