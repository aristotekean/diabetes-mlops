# frontend

Interfaz Streamlit (S7) para consultar el modelo productivo. Habla **solo** con la
API de inferencia (`API_URL`); nunca con MLflow ni con la base de datos.

## Funcionalidad

- Formulario con los 44 campos del contrato de entrada
  ([docs/data-contract.md](../../docs/data-contract.md)), agrupados por sección.
  Los campos categóricos que admiten faltantes muestran `(desconocido)` y se
  envían como `null`.
- **Valores de ejemplo**: tres encuentros reales del dataset (sin identificadores),
  con el resultado observado como referencia.
- **Predicción**: clase, probabilidades por clase, modelo, versión, alias, tiempo
  de procesamiento y `request_id`.
- **Errores de validación**: el formulario revisa códigos ICD-9 y rangos antes de
  enviar, y la pestaña *JSON (avanzado)* envía el cuerpo tal cual para mostrar los
  errores 422 de la API campo por campo (la API también registra esas solicitudes
  como fallidas en `inference.logs`).
- Barra lateral con el estado de la API y la versión del modelo cargado
  (`/health`, `/model-info`).

## Ejecución

Con el stack completo, desde la raíz del repositorio:

```bash
docker compose up -d --build frontend
# http://localhost:8501  (puerto configurable con FRONTEND_PORT)
```

## Pruebas

```bash
cd apps/frontend
uv sync
uv run pytest -q
```
