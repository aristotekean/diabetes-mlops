"""Tests for RAW validation rules and the inference input contract."""

import unittest

from diabetes_model.contract import (
    EXCLUSION_EXPIRED_OR_HOSPICE,
    INPUT_COLUMNS,
    NON_INPUT_COLUMNS,
    exclusion_reason,
    input_frame,
    prepare_input,
    validate_source_record,
)
from records import source_record


class SourceValidationTests(unittest.TestCase):
    def test_valid_record_has_no_errors(self) -> None:
        self.assertEqual(validate_source_record(source_record()), [])

    def test_unknown_gender_is_a_valid_source_value(self) -> None:
        record = source_record(gender="Unknown/Invalid")
        self.assertEqual(validate_source_record(record), [])

    def test_invalid_values_are_reported_by_column(self) -> None:
        record = source_record(
            readmitted="maybe",
            time_in_hospital="30",
            num_medications="many",
            age="[100-110)",
            insulin="Sometimes",
        )
        self.assertEqual(
            sorted(validate_source_record(record)),
            [
                "invalid_age",
                "invalid_insulin",
                "invalid_num_medications",
                "invalid_readmitted",
                "time_in_hospital_out_of_range",
            ],
        )

    def test_missing_columns_are_reported(self) -> None:
        record = source_record()
        del record["diag_1"]
        self.assertEqual(validate_source_record(record), ["missing_columns:diag_1"])

    def test_expired_and_hospice_discharges_are_excluded(self) -> None:
        for code in ("11", "13", "14", "19", "20", "21"):
            record = source_record(discharge_disposition_id=code)
            self.assertEqual(exclusion_reason(record), EXCLUSION_EXPIRED_OR_HOSPICE)
        self.assertIsNone(exclusion_reason(source_record()))


class InputContractTests(unittest.TestCase):
    def test_input_contract_excludes_identifiers_and_target(self) -> None:
        self.assertEqual(len(INPUT_COLUMNS), 44)
        self.assertTrue(NON_INPUT_COLUMNS.isdisjoint(INPUT_COLUMNS))
        self.assertIn("discharge_disposition_id", INPUT_COLUMNS)

    def test_prepare_input_types_and_missing_tokens(self) -> None:
        prepared = prepare_input(source_record())
        self.assertEqual(list(prepared), list(INPUT_COLUMNS))
        self.assertEqual(prepared["time_in_hospital"], 3.0)
        self.assertIsNone(prepared["payer_code"])
        self.assertEqual(prepared["max_glu_serum"], "None")
        self.assertEqual(prepared["admission_type_id"], "1")

    def test_prepare_input_accepts_api_style_values(self) -> None:
        prepared = prepare_input(
            {"admission_type_id": 1, "time_in_hospital": 4, "race": None}
        )
        self.assertEqual(prepared["admission_type_id"], "1")
        self.assertEqual(prepared["time_in_hospital"], 4.0)
        self.assertIsNone(prepared["race"])

    def test_prepare_input_rejects_non_numeric_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "num_procedures must be numeric"):
            prepare_input({"num_procedures": "three"})

    def test_input_frame_has_contract_columns(self) -> None:
        frame = input_frame([source_record(), source_record(payer_code="MC")])
        self.assertEqual(list(frame.columns), list(INPUT_COLUMNS))
        self.assertEqual(str(frame["num_medications"].dtype), "float64")
        self.assertIsNone(frame.loc[0, "payer_code"])


if __name__ == "__main__":
    unittest.main()
