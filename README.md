# diabetes-mlops

Plataforma MLOps de extremo a extremo para predecir el riesgo de diabetes. Cubre el ciclo completo: orquestación del entrenamiento, registro y versionado de modelos, servicio de inferencia, interfaz para el usuario final, monitoreo y pruebas de carga.

## Arquitectura

![Arquitectura de la plataforma](docs/images/1-arquitectura.png)

La plataforma se despliega sobre tres servidores conectados por una red Tailscale:

| Servidor | Rol | Componentes |
|----------|-----|-------------|
| Server A | Clúster Kubernetes | S1 PostgreSQL (metadatos de MLflow), S2 MLflow (gestor de modelos), S3 MinIO (almacén de artefactos), S4 FastAPI (API de inferencia), S5 PostgreSQL (datos de entrenamiento), S6 Airflow (orquestador de entrenamiento), S7 Streamlit (frontend), Ingress controller |
| Server B | Pruebas de carga | Locust |
| Server C | Monitoreo | S8 Prometheus (métricas), S9 Grafana (dashboards) |

Flujo principal: Airflow toma los datos de entrenamiento desde PostgreSQL, entrena y registra el modelo en MLflow, que persiste los artefactos en MinIO. La API de inferencia carga el modelo desde MLflow y expone las predicciones al frontend de Streamlit a través del Ingress. Prometheus recolecta las métricas del clúster y Grafana las visualiza.

## Estructura del repositorio

```
apps/            servicios propios: api-inference (FastAPI), frontend (Streamlit), airflow (DAGs)
ml/              paquete python compartido con la lógica del modelo
infra/k8s/       manifiestos kustomize por servicio
infra/monitoring observabilidad con Prometheus y Grafana (Server C)
infra/load-testing  pruebas de carga con Locust (Server B)
```
