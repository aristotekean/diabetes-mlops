from fastapi import FastAPI

app = FastAPI(title="Diabetes Inference API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Docker HEALTHCHECK and Kubernetes."""
    return {"status": "ok"}
