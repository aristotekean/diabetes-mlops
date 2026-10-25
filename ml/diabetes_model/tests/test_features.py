"""Tests for the readmission-features-v2 transformation."""

import json
import unittest

import pandas as pd

from diabetes_model.contract import input_frame
from diabetes_model.features import (
    FEATURE_COLUMNS,
    FeatureEngineer,
    engineer_features,
    feature_frame,
    features_to_json,
    icd9_group,
    specialty_group,
)
from records import source_record


class GroupingTests(unittest.TestCase):
    def test_icd9_groups(self) -> None:
        cases = {
            "428": "circulatory",
            "459.9": "circulatory",
            "785": "circulatory",
            "486": "respiratory",
            "250.83": "diabetes",
            "250": "diabetes",
            "250.9": "diabetes",
            "820": "injury",
            "715": "musculoskeletal",
            "599": "genitourinary",
            "174": "neoplasms",
            "V57": "other",
            "E909": "other",
            "780": "other",
            "?": None,
            None: None,
        }
        for code, group in cases.items():
            with self.subTest(code=code):
                self.assertEqual(icd9_group(code), group)

    def test_specialty_groups(self) -> None:
        self.assertEqual(specialty_group("Surgery-Cardiovascular/Thoracic"), "surgery")
        self.assertEqual(specialty_group("Orthopedics-Reconstructive"), "orthopedics")
        self.assertEqual(specialty_group("Cardiology"), "cardiology")
        self.assertEqual(specialty_group("Dermatology"), "other")
        self.assertIsNone(specialty_group("?"))


class EngineerFeaturesTests(unittest.TestCase):
    def test_engineered_values(self) -> None:
        features = engineer_features(input_frame([source_record()]))
        row = features.iloc[0]

        self.assertEqual(list(features.columns), list(FEATURE_COLUMNS))
        self.assertEqual(row["age_years"], 75.0)
        self.assertEqual(row["total_visits"], 3.0)
        self.assertEqual(row["num_active_medications"], 2.0)
        self.assertEqual(row["num_medication_changes"], 1.0)
        self.assertEqual(row["diag_1_group"], "circulatory")
        self.assertEqual(row["diag_2_group"], "diabetes")
        self.assertEqual(row["admission_type"], "emergency")
        self.assertEqual(row["admission_source"], "emergency_room")
        self.assertEqual(row["discharge_group"], "home")
        self.assertEqual(row["max_glu_serum"], "None")
        self.assertTrue(pd.isna(row["payer_code"]))

    def test_unknown_gender_and_missing_lab_results(self) -> None:
        inputs = input_frame([source_record(gender="Unknown/Invalid")])
        inputs.loc[0, "A1Cresult"] = None
        row = engineer_features(inputs).iloc[0]
        self.assertTrue(pd.isna(row["gender"]))
        self.assertEqual(row["A1Cresult"], "None")

    def test_unseen_codes_fall_back_to_a_group(self) -> None:
        row = engineer_features(
            input_frame([source_record(admission_source_id="99", discharge_disposition_id="99")])
        ).iloc[0]
        self.assertEqual(row["admission_source"], "other")
        self.assertEqual(row["discharge_group"], "other")

    def test_json_round_trip_preserves_features(self) -> None:
        features = engineer_features(input_frame([source_record(), source_record(race="?")]))
        payload = json.loads(json.dumps(features_to_json(features), allow_nan=False))
        rebuilt = feature_frame(payload)
        pd.testing.assert_frame_equal(rebuilt, features.reset_index(drop=True))

    def test_transformer_matches_function(self) -> None:
        inputs = input_frame([source_record()])
        pd.testing.assert_frame_equal(
            FeatureEngineer().fit_transform(inputs), engineer_features(inputs)
        )


if __name__ == "__main__":
    unittest.main()
