from frontend.examples import EXAMPLES
from frontend.fields import FIELDS, UNKNOWN, FIELDS_BY_NAME, from_widget_value, to_widget_value
from frontend.validation import validate_features

# docs/data-contract.md: 47 source columns minus identifiers, target and the
# three excluded columns.
EXCLUDED = {"encounter_id", "patient_nbr", "readmitted", "weight", "examide", "citoglipton"}


def test_form_covers_the_44_contract_inputs():
    names = [field.name for field in FIELDS]
    assert len(names) == 44
    assert len(set(names)) == 44
    assert not EXCLUDED & set(names)


def test_defaults_and_options_are_consistent():
    for field in FIELDS:
        if field.kind == "select":
            assert field.default in field.options, field.name
        if field.kind == "number":
            assert field.min_value <= field.default <= field.max_value, field.name


def test_examples_match_the_form_and_pass_validation():
    for name, example in EXAMPLES.items():
        features = example["features"]
        assert set(features) == set(FIELDS_BY_NAME), name
        assert validate_features(features) == [], name


def test_widget_round_trip_maps_missing_to_null():
    race = FIELDS_BY_NAME["race"]
    diag = FIELDS_BY_NAME["diag_3"]
    days = FIELDS_BY_NAME["time_in_hospital"]
    assert to_widget_value(race, None) == UNKNOWN
    assert from_widget_value(race, UNKNOWN) is None
    assert from_widget_value(diag, "   ") is None
    assert from_widget_value(diag, " V45 ") == "V45"
    assert from_widget_value(days, 3.0) == 3


def test_validation_reports_bad_codes_and_ranges():
    features = dict(next(iter(EXAMPLES.values()))["features"])
    features["diag_2"] = "abc"
    features["time_in_hospital"] = 20
    features["diag_1"] = None
    fields = {name for name, _ in validate_features(features)}
    assert fields == {"diag_1", "diag_2", "time_in_hospital"}
