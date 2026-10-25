"""Diabetes Inference API.

Besides the model-serving endpoints, the app exposes Prometheus metrics at
``/metrics`` so Server C (Prometheus + Grafana, see ``infra/monitoring``) can
scrape request rate, latency and error metrics, plus the process CPU/memory
gauges that ``prometheus_client`` registers by default.

Instrumentation is intentionally transport-level: every route the API gains
(``/predict``, ``/model-info``, …) is tracked automatically through the
``handler`` label, with no extra wiring per endpoint.
"""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator, metrics

app = FastAPI(title="Diabetes Inference API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Docker HEALTHCHECK and Kubernetes."""
    return {"status": "ok"}


# Prometheus instrumentation. Two explicit metrics, both labelled so the
# Grafana dashboard (infra/monitoring) can slice by endpoint and status:
#
#   http_requests_total{method,handler,status}   Counter
#       total requests, requests/sec, errors and error rate
#       (status grouped as "2xx"/"4xx"/"5xx").
#   http_request_duration_seconds{method,handler} Histogram
#       average latency (sum/count) and p50/p95/p99 (bucket + histogram_quantile).
#
# prometheus_client also registers process_* gauges on import, so the pod's
# CPU (process_cpu_seconds_total) and memory (process_resident_memory_bytes)
# are exported without extra wiring. Every route the API gains is tracked
# automatically through the `handler` label; /metrics excludes itself.
_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

(
    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/metrics"],
    )
    .add(metrics.requests(metric_name="http_requests_total"))
    .add(
        metrics.latency(
            metric_name="http_request_duration_seconds",
            buckets=_LATENCY_BUCKETS,
        )
    )
    .instrument(app)
    .expose(app, endpoint="/metrics", include_in_schema=True)
)
