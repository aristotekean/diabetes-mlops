"""Streamlit UI (S7): fill in an encounter, send it to the API, show the result.

Inference goes exclusively through the Inference API; the UI never talks to
MLflow or the database.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd
import streamlit as st

from frontend.client import ApiClient, ApiError
from frontend.examples import EXAMPLES
from frontend.fields import (
    FIELDS,
    FIELDS_BY_NAME,
    SECTIONS,
    default_values,
    from_widget_value,
    to_widget_value,
    widget_options,
)
from frontend.validation import validate_features

API_URL = os.environ.get("API_URL", "http://localhost:8000")

CLASS_LABELS = {
    "NO": "Sin readmisión",
    ">30": "Readmisión después de 30 días",
    "<30": "Readmisión en menos de 30 días",
}

st.set_page_config(page_title="Riesgo de readmisión · Diabetes", page_icon="🩺", layout="wide")


@st.cache_resource
def get_client() -> ApiClient:
    return ApiClient(API_URL)


def _key(name: str) -> str:
    return f"field_{name}"


def _set_form(values: dict[str, Any]) -> None:
    for name, value in values.items():
        st.session_state[_key(name)] = to_widget_value(FIELDS_BY_NAME[name], value)


def _init_state() -> None:
    if "initialised" not in st.session_state:
        _set_form(default_values())
        st.session_state.initialised = True
        st.session_state.result = None
        st.session_state.error = None


def _load_example() -> None:
    _set_form(EXAMPLES[st.session_state.example_choice]["features"])
    st.session_state.result = None
    st.session_state.error = None


def _reset_form() -> None:
    _set_form(default_values())
    st.session_state.result = None
    st.session_state.error = None


def _current_features() -> dict[str, Any]:
    return {
        field.name: from_widget_value(field, st.session_state[_key(field.name)])
        for field in FIELDS
    }


def _send(body: Any) -> None:
    st.session_state.result = None
    st.session_state.error = None
    try:
        st.session_state.result = get_client().predict_raw(body)
    except ApiError as exc:
        st.session_state.error = exc


def _sidebar() -> None:
    client = get_client()
    st.sidebar.header("Servicio")
    st.sidebar.caption(f"API: `{API_URL}`")
    try:
        health = client.health()
        st.sidebar.success("API disponible")
        if health.get("database") != "ok":
            st.sidebar.warning("La API no puede escribir el registro de inferencias.")
    except ApiError as exc:
        st.sidebar.error(f"API no disponible: {exc}")
        return
    try:
        info = client.model_info()
    except ApiError as exc:
        st.sidebar.error(str(exc))
        return
    st.sidebar.subheader("Modelo productivo")
    if info.get("status") == "ready":
        st.sidebar.metric("Versión", info.get("version"))
        st.sidebar.write(f"**{info.get('name')}** @ `{info.get('alias')}`")
        st.sidebar.caption(f"Cargado: {info.get('loaded_at')}")
    else:
        st.sidebar.warning(
            "No hay un modelo productivo cargado todavía. Ejecuta el DAG de "
            "entrenamiento en Airflow."
        )
        if info.get("last_error"):
            st.sidebar.caption(info["last_error"])
    st.sidebar.button("Actualizar estado")


def _render_form() -> None:
    with st.form("encounter"):
        for title, fields in SECTIONS:
            st.subheader(title)
            columns = st.columns(4)
            for index, field in enumerate(fields):
                column = columns[index % 4]
                key = _key(field.name)
                if field.kind == "select":
                    column.selectbox(field.label, widget_options(field), key=key, help=field.help)
                elif field.kind == "number":
                    column.number_input(
                        field.label, min_value=field.min_value, max_value=field.max_value,
                        step=1, key=key, help=field.help,
                    )
                else:
                    column.text_input(field.label, key=key, help=field.help)
        submitted = st.form_submit_button("Predecir", type="primary", width="stretch")
    if submitted:
        features = _current_features()
        errors = validate_features(features)
        if errors:
            st.session_state.result = None
            st.session_state.error = ApiError(
                "Corrige los campos marcados antes de enviar.", field_errors=errors
            )
        else:
            _send({"features": features})


def _render_json() -> None:
    st.caption(
        "Envía el cuerpo tal cual a `POST /predict`. Útil para ver cómo la API valida "
        "el contrato: prueba a quitar un campo, enviar un número como texto o agregar "
        "`patient_nbr`."
    )
    default = json.dumps({"features": _current_features()}, indent=2, ensure_ascii=False)
    body_text = st.text_area("Cuerpo JSON", value=default, height=420)
    if st.button("Enviar JSON"):
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError as exc:
            st.session_state.result = None
            st.session_state.error = ApiError(f"JSON inválido: {exc}")
        else:
            _send(body)


def _render_result() -> None:
    error: ApiError | None = st.session_state.error
    result: dict[str, Any] | None = st.session_state.result
    if error is not None:
        st.error(error.message)
        if error.field_errors:
            st.dataframe(
                pd.DataFrame(error.field_errors, columns=["Campo", "Error"]),
                hide_index=True, width="stretch",
            )
        if error.request_id:
            st.caption(f"request_id: `{error.request_id}` (registrado como fallido)")
        return
    if result is None:
        return

    prediction = result["prediction"]
    model = result["model"]
    st.success(f"Predicción: **{CLASS_LABELS.get(prediction, prediction)}** (`{prediction}`)")
    col_model, col_alias, col_time = st.columns(3)
    col_model.metric("Modelo", f"{model['name']} v{model['version']}")
    col_alias.metric("Alias", model["alias"])
    col_time.metric("Tiempo de procesamiento", f"{result['processing_time_ms']:.1f} ms")
    probabilities = result.get("probabilities") or {}
    if probabilities:
        frame = pd.DataFrame(
            {
                "Clase": [f"{CLASS_LABELS.get(c, c)} ({c})" for c in probabilities],
                "Probabilidad": list(probabilities.values()),
            }
        ).set_index("Clase")
        st.bar_chart(frame, horizontal=True)
    st.caption(f"request_id: `{result['request_id']}`")


def main() -> None:
    _init_state()
    _sidebar()

    st.title("Riesgo de readmisión hospitalaria en pacientes con diabetes")
    st.caption(
        "Predice si un encuentro hospitalario termina en readmisión antes de 30 días, "
        "después de 30 días o sin readmisión, usando el modelo productivo de MLflow."
    )

    col_choice, col_load, col_reset = st.columns([3, 1, 1], vertical_alignment="bottom")
    col_choice.selectbox("Valores de ejemplo", list(EXAMPLES), key="example_choice")
    col_load.button("Cargar ejemplo", on_click=_load_example, width="stretch")
    col_reset.button("Restablecer", on_click=_reset_form, width="stretch")
    observed = EXAMPLES[st.session_state.example_choice]["observed"]
    st.caption(f"Resultado observado en el dataset para este ejemplo: `{observed}`")

    tab_form, tab_json = st.tabs(["Formulario", "JSON (avanzado)"])
    with tab_form:
        _render_form()
    with tab_json:
        _render_json()

    st.divider()
    _render_result()


main()
