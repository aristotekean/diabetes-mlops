"""Load test for the Inference API: simulated users scoring encounters on /predict.

Run with the compose profile `loadtest` and open the UI on http://<host>:8089.
Every request carries the 44-field contract of docs/data-contract.md, built from
the value distribution of the Diabetes 130-US dataset, so the API does the same
work as with real traffic (validation, model scoring and one inference.logs row).

Finding the degradation point: set LOCUST_STEP_LOAD=1 and the run follows a
staircase instead of the users/spawn rate typed in the UI. Each step adds
LOCUST_STEP_USERS users and holds for LOCUST_STEP_SECONDS, up to
LOCUST_STEP_MAX_USERS; the step at which p95 latency or failures jump in Grafana
or in the Locust charts is the approximate limit for the configured resources.
"""

import os
import random

from locust import FastHttpUser, LoadTestShape, between, task

AGE_BUCKETS = [f"[{low}-{low + 10})" for low in range(0, 100, 10)]
AGE_WEIGHTS = [0.2, 0.7, 1.6, 3.7, 9.5, 17, 22, 25.6, 16.9, 2.7]
RACES = ["Caucasian", "AfricanAmerican", "Hispanic", "Asian", "Other", None]
RACE_WEIGHTS = [74.8, 18.9, 2.0, 0.6, 1.5, 2.2]
SPECIALTIES = [
    None, "InternalMedicine", "Emergency/Trauma", "Family/GeneralPractice", "Cardiology",
    "Surgery-General", "Nephrology", "Orthopedics", "Radiologist", "Pulmonology",
    "Psychiatry", "Urology", "Gastroenterology", "Oncology", "Neurology",
]
SPECIALTY_WEIGHTS = [49, 14.4, 7.4, 7.3, 5.3, 3, 1.6, 1.4, 1.1, 0.9, 0.8, 0.7, 0.5, 0.3, 0.2]
PAYER_CODES = [None, "MC", "HM", "SP", "BC", "MD", "CP", "UN", "CM"]
PAYER_WEIGHTS = [39.6, 31.9, 6.2, 4.9, 4.6, 3.5, 2.5, 2.4, 1.9]
DIAGNOSES = [
    "428", "414", "786", "410", "486", "427", "491", "715", "682", "434", "780", "996",
    "276", "38", "250.8", "599", "584", "V57", "250.6", "820", "403", "250.02", "250",
]
DISCHARGE_IDS = ["1", "1", "1", "1", "3", "6", "18", "2", "22", "5", "25", "4", "7"]
ADMISSION_TYPE_IDS = ["1", "1", "1", "3", "2", "6", "5"]
ADMISSION_SOURCE_IDS = ["7", "7", "1", "1", "17", "4", "6", "2"]
DOSES = ["No", "Steady", "Up", "Down"]
# Share of encounters where each drug is present at all (dataset frequencies).
MEDICATION_USE = {
    "metformin": 0.20, "repaglinide": 0.015, "nateglinide": 0.007, "chlorpropamide": 0.001,
    "glimepiride": 0.05, "acetohexamide": 0.0, "glipizide": 0.125, "glyburide": 0.105,
    "tolbutamide": 0.0, "pioglitazone": 0.072, "rosiglitazone": 0.063, "acarbose": 0.003,
    "miglitol": 0.0, "troglitazone": 0.0, "tolazamide": 0.0, "insulin": 0.53,
    "glyburide-metformin": 0.007, "glipizide-metformin": 0.0, "glimepiride-pioglitazone": 0.0,
    "metformin-rosiglitazone": 0.0, "metformin-pioglitazone": 0.0,
}


def _count(mean: float, high: int) -> int:
    """Skewed small counts (visits, procedures) clipped to the observed range."""
    return min(high, int(random.expovariate(1 / mean))) if mean else 0


def random_encounter() -> dict:
    """One encounter inside the ranges and categories of the training data."""
    medications = {
        name: (random.choice(DOSES[1:]) if random.random() < share else "No")
        for name, share in MEDICATION_USE.items()
    }
    on_medication = any(dose != "No" for dose in medications.values())
    return {
        "race": random.choices(RACES, RACE_WEIGHTS)[0],
        "gender": random.choice(["Female", "Male"]),
        "age": random.choices(AGE_BUCKETS, AGE_WEIGHTS)[0],
        "admission_type_id": random.choice(ADMISSION_TYPE_IDS),
        "discharge_disposition_id": random.choice(DISCHARGE_IDS),
        "admission_source_id": random.choice(ADMISSION_SOURCE_IDS),
        "time_in_hospital": random.randint(1, 14),
        "payer_code": random.choices(PAYER_CODES, PAYER_WEIGHTS)[0],
        "medical_specialty": random.choices(SPECIALTIES, SPECIALTY_WEIGHTS)[0],
        "num_lab_procedures": max(1, min(132, int(random.gauss(43, 20)))),
        "num_procedures": random.randint(0, 6),
        "num_medications": max(1, min(81, int(random.gauss(16, 8)))),
        "number_outpatient": _count(0.4, 42),
        "number_emergency": _count(0.2, 76),
        "number_inpatient": _count(0.6, 21),
        "diag_1": random.choice(DIAGNOSES),
        "diag_2": random.choice(DIAGNOSES),
        "diag_3": random.choice(DIAGNOSES + [None]),
        "number_diagnoses": random.randint(1, 16),
        "max_glu_serum": random.choices(["None", "Norm", ">200", ">300"], [94.7, 2.6, 1.5, 1.2])[0],
        "A1Cresult": random.choices(["None", "Norm", ">7", ">8"], [83.3, 4.9, 3.7, 8.1])[0],
        **medications,
        "change": random.choice(["No", "Ch"]) if on_medication else "No",
        "diabetesMed": "Yes" if on_medication else "No",
    }


class InferenceUser(FastHttpUser):
    """A client application scoring one encounter at a time."""

    wait_time = between(0.5, 1.5)

    @task(20)
    def predict(self) -> None:
        with self.client.post(
            "/predict", json={"features": random_encounter()}, name="/predict",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            body = response.json()
            if "prediction" not in body or "model" not in body:
                response.failure("response without prediction/model")

    @task(1)
    def model_info(self) -> None:
        self.client.get("/model-info")


if os.environ.get("LOCUST_STEP_LOAD") == "1":

    class StepLoadShape(LoadTestShape):
        """Staircase: +step_users every step_seconds until max_users, then stop."""

        step_users = int(os.environ.get("LOCUST_STEP_USERS", "10"))
        step_seconds = int(os.environ.get("LOCUST_STEP_SECONDS", "60"))
        max_users = int(os.environ.get("LOCUST_STEP_MAX_USERS", "100"))

        def tick(self):
            step = int(self.get_run_time() // self.step_seconds) + 1
            users = step * self.step_users
            if users > self.max_users:
                return None
            return users, self.step_users
