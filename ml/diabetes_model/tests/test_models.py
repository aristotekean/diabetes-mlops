"""Tests for patient splits, candidate models, and the served pipeline."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd
import skops.io

from diabetes_model.contract import input_frame
from diabetes_model.features import engineer_features
from diabetes_model.models import (
    CANDIDATES,
    build_candidate,
    build_serving_pipeline,
    evaluate,
    evaluate_predictions,
)
from diabetes_model.splits import assign_split
from diabetes_model.training import best_candidate, prepare_dataset, train_candidates
from records import source_records


class SplitTests(unittest.TestCase):
    def test_split_is_deterministic_and_order_independent(self) -> None:
        patients = [str(number) for number in range(2_000)]
        first = {patient: assign_split(patient) for patient in patients}
        second = {patient: assign_split(patient) for patient in reversed(patients)}
        self.assertEqual(first, second)

    def test_split_proportions_are_close_to_70_15_15(self) -> None:
        counts = pd.Series(
            [assign_split(str(number)) for number in range(20_000)]
        ).value_counts(normalize=True)
        self.assertAlmostEqual(counts["train"], 0.70, delta=0.01)
        self.assertAlmostEqual(counts["validation"], 0.15, delta=0.01)
        self.assertAlmostEqual(counts["test"], 0.15, delta=0.01)

    def test_split_requires_patient(self) -> None:
        with self.assertRaises(ValueError):
            assign_split(" ")


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        records = source_records()
        cls.inputs = input_frame(records)
        cls.features = engineer_features(cls.inputs)
        cls.target = pd.Series([r["readmitted"] for r in records], dtype="string")

    def test_every_candidate_fits_and_reports_metrics(self) -> None:
        for name in CANDIDATES:
            with self.subTest(candidate=name):
                model = build_candidate(name).fit(self.features, self.target)
                metrics = evaluate(model, self.features, self.target, prefix="validation")
                self.assertIn("validation_macro_f1", metrics)
                self.assertIn("validation_roc_auc_ovr_macro", metrics)
                self.assertIn("validation_recall_readmitted_within_30_days", metrics)
                self.assertTrue(
                    all(c.isalnum() or c == "_" for name in metrics for c in name)
                )

    def test_serving_pipeline_accepts_contract_inputs(self) -> None:
        model = build_candidate("logistic_regression").fit(self.features, self.target)
        serving = build_serving_pipeline(model)
        pd.testing.assert_series_equal(
            pd.Series(serving.predict(self.inputs)), pd.Series(model.predict(self.features))
        )

    def test_serving_pipeline_round_trips_through_skops(self) -> None:
        model = build_candidate("hist_gradient_boosting").fit(self.features, self.target)
        serving = build_serving_pipeline(model)
        payload = skops.io.dumps(serving)
        untrusted = skops.io.get_untrusted_types(data=payload)
        self.assertIn("diabetes_model.features.FeatureEngineer", untrusted)
        loaded = skops.io.loads(payload, trusted=untrusted)
        self.assertEqual(list(loaded.predict(self.inputs)), list(serving.predict(self.inputs)))

    def test_label_metrics_handle_missing_classes(self) -> None:
        metrics = evaluate_predictions(
            pd.Series(["NO", "NO"]), ["NO", "<30"], prefix="validation"
        )
        self.assertEqual(metrics["validation_recall_no_readmission"], 0.5)


class OfflineTrainingTests(unittest.TestCase):
    def test_prepare_dataset_applies_dag_rules(self) -> None:
        records = source_records()
        records[0]["discharge_disposition_id"] = "11"
        records[1]["readmitted"] = "maybe"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diabetes.csv"
            pd.DataFrame(records).to_csv(path, index=False)
            data = prepare_dataset(path)
        self.assertEqual(data.rejected_rows, 1)
        self.assertEqual(data.excluded_rows, 1)
        self.assertEqual(len(data.target), len(records) - 2)
        self.assertEqual(set(data.split), {"train", "validation", "test"})

    def test_train_candidates_selects_by_validation_macro_f1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diabetes.csv"
            pd.DataFrame(source_records(patients=90)).to_csv(path, index=False)
            data = prepare_dataset(path)
        results = train_candidates(data, ("logistic_regression", "random_forest"))
        best = best_candidate(results)
        self.assertEqual(
            results[best][1]["validation_macro_f1"],
            max(metrics["validation_macro_f1"] for _, metrics in results.values()),
        )


if __name__ == "__main__":
    unittest.main()
