"""Example encounters for the "load example" buttons.

Real rows of Diabetes 130-US hospitals with the identifiers, the target and the
excluded columns removed, so they match the 44-field contract. ``observed`` is
the outcome recorded in the dataset, shown only as a reference.
"""

from __future__ import annotations

from typing import Any

_NO_MEDICATION = {
    name: "No"
    for name in (
        "metformin", "repaglinide", "nateglinide", "chlorpropamide", "glimepiride",
        "acetohexamide", "glipizide", "glyburide", "tolbutamide", "pioglitazone",
        "rosiglitazone", "acarbose", "miglitol", "troglitazone", "tolazamide", "insulin",
        "glyburide-metformin", "glipizide-metformin", "glimepiride-pioglitazone",
        "metformin-rosiglitazone", "metformin-pioglitazone",
    )
}

EXAMPLES: dict[str, dict[str, Any]] = {
    "Medicina interna · 70-80 años · 10 días": {
        "observed": "NO",
        "features": {
            "race": "Caucasian", "gender": "Male", "age": "[70-80)",
            "admission_type_id": "1", "discharge_disposition_id": "2",
            "admission_source_id": "7", "time_in_hospital": 10, "payer_code": "MC",
            "medical_specialty": "InternalMedicine", "num_lab_procedures": 68,
            "num_procedures": 1, "num_medications": 18, "number_outpatient": 0,
            "number_emergency": 0, "number_inpatient": 0, "diag_1": "682",
            "diag_2": "427", "diag_3": "276", "number_diagnoses": 6,
            "max_glu_serum": "None", "A1Cresult": "None",
            **_NO_MEDICATION, "metformin": "Steady", "insulin": "Steady",
            "change": "Ch", "diabetesMed": "Yes",
        },
    },
    "Cardiología · 30-40 años · HbA1c >8": {
        "observed": ">30",
        "features": {
            "race": "Caucasian", "gender": "Male", "age": "[30-40)",
            "admission_type_id": "1", "discharge_disposition_id": "1",
            "admission_source_id": "7", "time_in_hospital": 10, "payer_code": "MC",
            "medical_specialty": "Cardiology", "num_lab_procedures": 105,
            "num_procedures": 2, "num_medications": 40, "number_outpatient": 6,
            "number_emergency": 0, "number_inpatient": 0, "diag_1": "250.13",
            "diag_2": "518", "diag_3": "482", "number_diagnoses": 9,
            "max_glu_serum": "None", "A1Cresult": ">8",
            **_NO_MEDICATION, "insulin": "Up",
            "change": "Ch", "diabetesMed": "Yes",
        },
    },
    "Emergencias · 70-80 años · 2 hospitalizaciones previas": {
        "observed": "<30",
        "features": {
            "race": "Caucasian", "gender": "Female", "age": "[70-80)",
            "admission_type_id": "1", "discharge_disposition_id": "1",
            "admission_source_id": "7", "time_in_hospital": 2, "payer_code": "SP",
            "medical_specialty": "Emergency/Trauma", "num_lab_procedures": 28,
            "num_procedures": 0, "num_medications": 19, "number_outpatient": 0,
            "number_emergency": 0, "number_inpatient": 2, "diag_1": "428",
            "diag_2": "V45", "diag_3": "414", "number_diagnoses": 9,
            "max_glu_serum": "None", "A1Cresult": "None",
            **_NO_MEDICATION, "insulin": "Up",
            "change": "Ch", "diabetesMed": "Yes",
        },
    },
}
