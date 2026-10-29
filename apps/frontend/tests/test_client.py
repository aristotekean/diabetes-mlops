import pytest
import requests

from frontend.client import ApiClient, ApiError, parse_validation_errors


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return self.response

    def get(self, url, timeout):
        self.calls.append((url, None))
        if self.exc:
            raise self.exc
        return self.response


def test_parse_validation_errors_collapses_union_members():
    detail = [
        {"loc": ["body", "features", "time_in_hospital", "float"], "msg": "not a number"},
        {"loc": ["body", "features", "time_in_hospital", "int"], "msg": "not an integer"},
        {"loc": ["body", "features", "patient_nbr"], "msg": "Extra inputs are not permitted"},
        {"loc": ["body", "features"], "msg": "Field required"},
    ]
    assert parse_validation_errors(detail) == [
        ("time_in_hospital", "not a number"),
        ("patient_nbr", "Extra inputs are not permitted"),
        ("body", "Field required"),
    ]


def test_predict_wraps_features_and_returns_payload():
    session = FakeSession(FakeResponse(200, {"prediction": "NO"}))
    client = ApiClient("http://api:8000/", session=session)
    assert client.predict({"age": "[60-70)"}) == {"prediction": "NO"}
    assert session.calls == [("http://api:8000/predict", {"features": {"age": "[60-70)"}})]


def test_predict_422_exposes_field_errors_and_request_id():
    payload = {
        "request_id": "abc",
        "detail": [{"loc": ["body", "features", "age"], "msg": "Field required"}],
    }
    client = ApiClient("http://api", session=FakeSession(FakeResponse(422, payload)))
    with pytest.raises(ApiError) as caught:
        client.predict({})
    assert caught.value.status_code == 422
    assert caught.value.request_id == "abc"
    assert caught.value.field_errors == [("age", "Field required")]


def test_predict_503_uses_api_message():
    payload = {"request_id": "x", "detail": {"error_code": "model_unavailable", "message": "no model"}}
    client = ApiClient("http://api", session=FakeSession(FakeResponse(503, payload)))
    with pytest.raises(ApiError, match="no model") as caught:
        client.predict({})
    assert caught.value.status_code == 503


def test_connection_errors_become_api_errors():
    client = ApiClient("http://api", session=FakeSession(exc=requests.ConnectionError("down")))
    with pytest.raises(ApiError):
        client.predict({})
    with pytest.raises(ApiError):
        client.health()
