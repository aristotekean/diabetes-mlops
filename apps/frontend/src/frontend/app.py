import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Diabetes Risk", page_icon="🩺")
st.title("Diabetes Risk Prediction")


def api_health() -> tuple[bool, str]:
    """Return whether the inference API answers its health probe."""
    try:
        response = requests.get(f"{API_URL}/health", timeout=3)
        response.raise_for_status()
        return True, response.json().get("status", "unknown")
    except requests.RequestException as exc:
        return False, str(exc)


healthy, detail = api_health()
if healthy:
    st.success(f"Inference API is up ({detail})")
else:
    st.error(f"Inference API unreachable at {API_URL}: {detail}")
