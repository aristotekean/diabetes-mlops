# Título
feat(monitoring): observabilidad con Prometheus + Grafana sobre Tailscale (Server C)

# Cuerpo

## Qué hace
Agrega el stack de observabilidad (Server C) para la API de inferencia: Prometheus recolecta las métricas de `/metrics` y Grafana las visualiza. El monitoreo se conecta a la red **Tailscale** (`aristotekean.github`) para alcanzar la API de Server A sobre la tailnet.

## Cambios
**API (`apps/api-inference`)**
- Instrumentada con `prometheus-fastapi-instrumentator`; expone `/metrics`.
- Métricas: `http_requests_total{method,handler,status}`, histograma `http_request_duration_seconds` y gauges `process_cpu_seconds_total` / `process_resident_memory_bytes`.
- `uv.lock` regenerado.

**Monitoreo (`infra/monitoring`)**
- `prometheus/prometheus.yml` con `file_sd` → target editable en `prometheus/targets/api-targets.yml`.
- Grafana con datasource y dashboard aprovisionados (**Diabetes · Inference API**): total de solicitudes, req/s, latencia promedio y p50/p95/p99, errores y tasa de error, CPU y memoria.
- `docker-compose.yml` (red Docker local) incluido en el compose raíz.
- `docker-compose.tailscale.yml`: sidecar Tailscale con Prometheus/Grafana compartiendo su namespace de red (Server C = nodo `monitoring` en la tailnet).
- `.env.example` con `TS_AUTHKEY`, `TS_HOSTNAME`, puertos y admin.
- README con uso local y despliegue en Server C vía Tailscale.

## Cómo probar
Local (todo en una máquina):
```bash
docker compose up -d --build
# Grafana http://localhost:3000 (admin/admin), Prometheus http://localhost:9090
# generar tráfico: docker compose --profile loadtest up -d
```
Server C con Tailscale:
```bash
cd infra/monitoring && cp .env.example .env   # setear TS_AUTHKEY
# editar prometheus/targets/api-targets.yml -> server-a:8000
docker compose -f docker-compose.tailscale.yml up -d
```

## Notas
- `infra/load-testing/locustfile.py` aún referencia *covertype* y `/predict` (la API todavía no lo expone): adaptarlo a diabetes en un PR posterior para la demo de carga.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01HREX9gonv5UPepAoepCinV
