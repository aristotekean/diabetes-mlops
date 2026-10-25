"""Load test for the Inference API, sent through the Nginx load balancer.

Run with the compose profile `loadtest` and open the UI on http://<host>:8089.
"""

import random

from locust import FastHttpUser, between, task

# Categories seen by the production model (training.covertype_features)
WILDERNESS_AREAS = ["commanche", "rawah"]
SOIL_TYPES = [
    "c2703", "c2705", "c4703", "c4704", "c4758", "c6102", "c7101", "c7102",
    "c7103", "c7201", "c7202", "c7700", "c7702", "c7745", "c7746", "c7755",
    "c7756", "c7757", "c7790", "c8703", "c8707", "c8708", "c8771", "c8772",
    "c8776",
]


def random_row() -> dict:
    """One observation inside the ranges of training.covertype_features."""
    return {
        "elevation": random.randint(2440, 3677),
        "aspect": random.randint(0, 360),
        "slope": random.randint(0, 51),
        "horizontal_distance_to_hydrology": random.randint(0, 1397),
        "vertical_distance_to_hydrology": random.randint(-132, 422),
        "horizontal_distance_to_roadways": random.randint(0, 3628),
        "hillshade_9am": random.randint(0, 254),
        "hillshade_noon": random.randint(0, 254),
        "hillshade_3pm": random.randint(0, 254),
        "horizontal_distance_to_fire_points": random.randint(0, 3664),
        "wilderness_area": random.choice(WILDERNESS_AREAS),
        "soil_type": random.choice(SOIL_TYPES),
    }


class InferenceUser(FastHttpUser):
    wait_time = between(0.5, 1.5)

    @task(10)
    def predict_one(self) -> None:
        self._predict(1, "/predict [1 row]")

    @task(2)
    def predict_batch(self) -> None:
        self._predict(20, "/predict [20 rows]")

    @task(1)
    def health(self) -> None:
        self.client.get("/health")

    def _predict(self, rows: int, name: str) -> None:
        payload = {"instances": [random_row() for _ in range(rows)]}
        with self.client.post(
            "/predict", json=payload, name=name, catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
            elif len(response.json()["predictions"]) != rows:
                response.failure("wrong number of predictions")