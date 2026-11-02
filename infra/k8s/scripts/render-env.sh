#!/usr/bin/env bash
# Render the stack configuration in .env (repository root) into the kustomize
# component infra/k8s/generated, which infra/k8s/kustomization.yaml lists under
# `components:`. The component holds:
#
#   - one env file + generator per ConfigMap / Secret the manifests reference
#     (mlflow-client-env, minio-env, minio-credentials, postgres-train-credentials, ...);
#   - the `images:` block, so DOCKERHUB_USER / IMAGE_TAG pick the image tags;
#   - a merge of the .env values into the `airflow-env` ConfigMap, whose fixed
#     Airflow settings stay in base/airflow/kustomization.yaml.
#
# Why rendered: kustomize's load restrictions only let a generator read files
# under its own root (infra/k8s), so it cannot read the root .env, and one
# .env feeds several objects under other key names (POSTGRES_USER from
# TRAIN_DB_USER, AWS_ACCESS_KEY_ID from MINIO_ROOT_USER, ...).
#
# The output contains credentials, so it is git-ignored. Re-run it after every
# change to .env, then apply:
#
#   bash infra/k8s/scripts/render-env.sh             # reads <repo>/.env
#   bash infra/k8s/scripts/render-env.sh other.env   # another settings file
#   kubectl apply -k infra/k8s
#
# The generated objects carry a content hash in their name, so a changed value
# rolls the pods that use it on the next apply.
#
# Written for bash 3.2 (the one macOS ships) as well as Linux bash.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV_FILE="${1:-$ROOT/.env}"
OUT="$ROOT/infra/k8s/generated"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "missing $ENV_FILE: create it with 'cp .env.example .env' and set the values" >&2
    exit 1
fi

# Same file docker compose reads; values with spaces or & are single-quoted
# there, which bash understands too.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

REQUIRED="DOCKERHUB_USER IMAGE_TAG
MLFLOW_DB_NAME MLFLOW_DB_USER MLFLOW_DB_PASSWORD
MINIO_ROOT_USER MINIO_ROOT_PASSWORD MLFLOW_ARTIFACTS_BUCKET MINIO_PROMETHEUS_AUTH_TYPE
MLFLOW_TRACKING_URI MLFLOW_S3_ENDPOINT_URL MLFLOW_EXPERIMENT_NAME
MODEL_NAME MODEL_ALIAS MODEL_REFRESH_SECONDS
TRAIN_DB_HOST TRAIN_DB_PORT TRAIN_DB_NAME TRAIN_DB_USER TRAIN_DB_PASSWORD
AIRFLOW_DB_NAME AIRFLOW_DB_USER AIRFLOW_DB_PASSWORD
AIRFLOW_ADMIN_USER AIRFLOW_ADMIN_PASSWORD AIRFLOW_JWT_SECRET
DIABETES_TRAINING_SCHEDULE DIABETES_DATASET_PATH DIABETES_SOURCE_URI
API_URL"
# Must be present in .env but may be empty.
OPTIONAL="MLFLOW_ALLOWED_HOSTS MLFLOW_CORS_ALLOWED_ORIGINS AIRFLOW_FERNET_KEY"

missing=""
for name in $REQUIRED; do
    [[ -n "${!name:-}" ]] || missing="$missing $name"
done
for name in $OPTIONAL; do
    [[ -n "${!name+set}" ]] || missing="$missing $name"
done
if [[ -n "$missing" ]]; then
    echo "missing in $ENV_FILE:$missing (see .env.example)" >&2
    exit 1
fi

# write_env FILE KEY... : one KEY=value line per variable, value taken from the
# variable of the same name.
write_env() {
    local file="$OUT/$1" key
    shift
    : > "$file"
    for key in "$@"; do
        printf '%s=%s\n' "$key" "${!key}" >> "$file"
    done
}

# write_pairs FILE KEY=VALUE... : lines given verbatim, for keys renamed on
# the way (the Postgres image reads POSTGRES_*, S3 clients read AWS_*).
write_pairs() {
    local file="$OUT/$1"
    shift
    printf '%s\n' "$@" > "$file"
}

rm -rf "$OUT"
mkdir -p "$OUT"

# ConfigMaps.
write_env mlflow-client-env.env MLFLOW_TRACKING_URI MLFLOW_S3_ENDPOINT_URL
write_env minio-env.env MINIO_PROMETHEUS_AUTH_TYPE
write_env mlflow-server-env.env MLFLOW_ARTIFACTS_BUCKET MLFLOW_ALLOWED_HOSTS MLFLOW_CORS_ALLOWED_ORIGINS
write_env api-inference-env.env MODEL_NAME MODEL_ALIAS MODEL_REFRESH_SECONDS TRAIN_DB_HOST TRAIN_DB_PORT
write_env frontend-env.env API_URL
write_env airflow-env.env TRAIN_DB_HOST TRAIN_DB_PORT DIABETES_DATASET_PATH DIABETES_SOURCE_URI \
    MLFLOW_EXPERIMENT_NAME MODEL_NAME MODEL_ALIAS DIABETES_TRAINING_SCHEDULE

# Secrets.
write_pairs minio-credentials.env \
    "MINIO_ROOT_USER=$MINIO_ROOT_USER" \
    "MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD" \
    "AWS_ACCESS_KEY_ID=$MINIO_ROOT_USER" \
    "AWS_SECRET_ACCESS_KEY=$MINIO_ROOT_PASSWORD"
write_pairs mlflow-db-credentials.env \
    "POSTGRES_DB=$MLFLOW_DB_NAME" \
    "POSTGRES_USER=$MLFLOW_DB_USER" \
    "POSTGRES_PASSWORD=$MLFLOW_DB_PASSWORD"
write_pairs postgres-train-credentials.env \
    "POSTGRES_DB=$TRAIN_DB_NAME" \
    "POSTGRES_USER=$TRAIN_DB_USER" \
    "POSTGRES_PASSWORD=$TRAIN_DB_PASSWORD"
write_pairs airflow-db-credentials.env \
    "POSTGRES_DB=$AIRFLOW_DB_NAME" \
    "POSTGRES_USER=$AIRFLOW_DB_USER" \
    "POSTGRES_PASSWORD=$AIRFLOW_DB_PASSWORD"
# The host:port is the `airflow-db` Service (base/postgres-airflow).
write_pairs airflow-secrets.env \
    "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://$AIRFLOW_DB_USER:$AIRFLOW_DB_PASSWORD@airflow-db:5432/$AIRFLOW_DB_NAME" \
    "AIRFLOW__CORE__FERNET_KEY=$AIRFLOW_FERNET_KEY" \
    "AIRFLOW__API_AUTH__JWT_SECRET=$AIRFLOW_JWT_SECRET"
write_pairs airflow-admin-user.env \
    "_AIRFLOW_WWW_USER_USERNAME=$AIRFLOW_ADMIN_USER" \
    "_AIRFLOW_WWW_USER_PASSWORD=$AIRFLOW_ADMIN_PASSWORD"

cat > "$OUT/kustomization.yaml" <<EOF
# GENERATED by infra/k8s/scripts/render-env.sh from .env; do not edit, re-run
# the script instead.
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component

configMapGenerator:
  - name: mlflow-client-env
    envs: [mlflow-client-env.env]
  - name: mlflow-server-env
    envs: [mlflow-server-env.env]
  - name: minio-env
    envs: [minio-env.env]
  - name: api-inference-env
    envs: [api-inference-env.env]
  - name: frontend-env
    envs: [frontend-env.env]
  # Adds the .env values to the fixed settings of base/airflow.
  - name: airflow-env
    behavior: merge
    envs: [airflow-env.env]

secretGenerator:
  - name: minio-credentials
    envs: [minio-credentials.env]
  - name: mlflow-db-credentials
    envs: [mlflow-db-credentials.env]
  - name: postgres-train-credentials
    envs: [postgres-train-credentials.env]
  - name: airflow-db-credentials
    envs: [airflow-db-credentials.env]
  - name: airflow-secrets
    envs: [airflow-secrets.env]
  - name: airflow-admin-user
    envs: [airflow-admin-user.env]

# The manifests name the images without a tag; this rewrites each one to
# DOCKERHUB_USER/<image>:IMAGE_TAG.
images:
  - name: aristotekean/diabetes-api-inference
    newName: $DOCKERHUB_USER/diabetes-api-inference
    newTag: "$IMAGE_TAG"
  - name: aristotekean/diabetes-frontend
    newName: $DOCKERHUB_USER/diabetes-frontend
    newTag: "$IMAGE_TAG"
  - name: aristotekean/diabetes-airflow
    newName: $DOCKERHUB_USER/diabetes-airflow
    newTag: "$IMAGE_TAG"
EOF

echo "rendered ${OUT#"$ROOT"/} from $ENV_FILE"
