"""Synthetic source records shared by the test modules."""

from diabetes_model.contract import MEDICATION_COLUMNS, SOURCE_COLUMNS, TARGET_CLASSES


def source_record(**overrides: str) -> dict[str, str]:
    """One valid CSV row, as strings, with optional field overrides."""
    record = {
        "encounter_id": "1001",
        "patient_nbr": "501",
        "race": "Caucasian",
        "gender": "Female",
        "age": "[70-80)",
        "weight": "?",
        "admission_type_id": "1",
        "discharge_disposition_id": "1",
        "admission_source_id": "7",
        "time_in_hospital": "3",
        "payer_code": "?",
        "medical_specialty": "InternalMedicine",
        "num_lab_procedures": "41",
        "num_procedures": "0",
        "num_medications": "12",
        "number_outpatient": "1",
        "number_emergency": "0",
        "number_inpatient": "2",
        "diag_1": "428",
        "diag_2": "250.01",
        "diag_3": "V45",
        "number_diagnoses": "9",
        "max_glu_serum": "None",
        "A1Cresult": ">8",
        "examide": "No",
        "citoglipton": "No",
        "change": "Ch",
        "diabetesMed": "Yes",
        "readmitted": "NO",
    }
    for column in MEDICATION_COLUMNS:
        record.setdefault(column, "No")
    record["insulin"] = "Up"
    record["metformin"] = "Steady"
    record.update(overrides)
    assert set(record) == set(SOURCE_COLUMNS)
    return record


def source_records(patients: int = 60, encounters_per_patient: int = 2) -> list[dict[str, str]]:
    """Enough varied records for every split to contain all target classes."""
    records = []
    for patient in range(patients):
        for encounter in range(encounters_per_patient):
            records.append(
                source_record(
                    encounter_id=str(patient * 100 + encounter),
                    patient_nbr=str(10_000 + patient),
                    readmitted=TARGET_CLASSES[(patient + encounter) % 3],
                    time_in_hospital=str(1 + (patient + encounter) % 14),
                    num_lab_procedures=str(20 + patient + encounter),
                    payer_code="?" if encounter else "MC",
                    gender="Male" if patient % 2 else "Female",
                    medical_specialty="?" if patient % 3 == 0 else "Cardiology",
                )
            )
    return records
