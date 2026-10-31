#!/bin/sh
# Render infra/database (migrate.sh + migrations/*.sql) into the committed
# ConfigMap manifest infra/k8s/base/postgres-train/migrations-configmap.yaml,
# which the `train-db-migrate` Job mounts at /database.
#
# Why a generated, committed file: kustomize's load restrictions only let a
# configMapGenerator read files under its own kustomization root (infra/k8s),
# and infra/database is a sibling directory. `--load-restrictor
# LoadRestrictionsNone` would unlock it but breaks a plain
# `kubectl apply -k infra/k8s`, so the ConfigMap is rendered here instead.
#
# MUST be re-run whenever infra/database/migrate.sh or any
# infra/database/migrations/*.sql changes, and the result committed:
#
#   bash infra/k8s/scripts/render-migrations.sh          # rewrite the manifest
#   bash infra/k8s/scripts/render-migrations.sh --check  # exit 1 if out of date
#
# `--check` is meant for CI / pre-commit: it renders to a temporary file and
# diffs it against the committed manifest without touching the repository.
#
# Requires kubectl (client-side only: --dry-run=client, no cluster access).
set -eu

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SOURCE_DIR="$ROOT/infra/database"
TARGET="$ROOT/infra/k8s/base/postgres-train/migrations-configmap.yaml"
TARGET_REL="infra/k8s/base/postgres-train/migrations-configmap.yaml"

usage() {
  echo "usage: $0 [--check]" >&2
  exit 2
}

MODE="write"
if [ "$#" -gt 1 ]; then
  usage
elif [ "$#" -eq 1 ]; then
  case "$1" in
    --check) MODE="check" ;;
    *) usage ;;
  esac
fi

for required in "$SOURCE_DIR/migrate.sh" "$SOURCE_DIR/migrations"; do
  if [ ! -e "$required" ]; then
    echo "missing source: $required" >&2
    exit 1
  fi
done

render() {
  cat <<'HEADER'
# GENERATED FILE - DO NOT EDIT BY HAND.
#
# Rendered from infra/database (migrate.sh + migrations/*.sql) by
# infra/k8s/scripts/render-migrations.sh. Re-run that script and commit the
# result whenever infra/database changes; `--check` reports drift.
#
# Data keys are file basenames, so migrate.sh and every *.sql end up side by
# side under the mount point. The train-db-migrate Job mounts this ConfigMap at
# /database and sets MIGRATIONS_DIR=/database to match that flat layout.
HEADER
  # POSIX sh has no pipefail, so kubectl is NOT piped into awk: its output is
  # captured first and any failure (kubectl missing, unreadable source file,
  # bad flag) aborts the render. Otherwise a failed kubectl would leave a
  # header-only manifest that write mode copies over the committed ConfigMap
  # while reporting success.
  if ! kubectl create configmap train-db-migrations \
      --from-file="$SOURCE_DIR/migrate.sh" \
      --from-file="$SOURCE_DIR/migrations" \
      --dry-run=client -o yaml > "$RAW"; then
    echo "error: kubectl create configmap failed; nothing was rendered" >&2
    exit 1
  fi
  if ! grep -q '^kind: ConfigMap$' "$RAW"; then
    echo "error: kubectl output is not a ConfigMap manifest; nothing was rendered" >&2
    exit 1
  fi
  # kubectl emits deterministic YAML (sorted keys); only the empty
  # creationTimestamp is dropped and the per-resource name label added.
  awk '
      /^  creationTimestamp: null$/ { next }
      { print }
      /^  name: train-db-migrations$/ {
        print "  labels:"
        print "    app.kubernetes.io/name: train-db-migrate"
      }
    ' "$RAW"
}

TMP="$(mktemp "${TMPDIR:-/tmp}/render-migrations.XXXXXX")"
RAW="$(mktemp "${TMPDIR:-/tmp}/render-migrations-raw.XXXXXX")"
trap 'rm -f "$TMP" "$RAW"' EXIT

render > "$TMP"

if [ "$MODE" = "check" ]; then
  if [ ! -f "$TARGET" ]; then
    echo "out of date: $TARGET_REL does not exist; run $0" >&2
    exit 1
  fi
  if ! diff -u "$TARGET" "$TMP"; then
    echo "out of date: $TARGET_REL differs from infra/database; run $0" >&2
    exit 1
  fi
  echo "up to date: $TARGET_REL"
else
  cp "$TMP" "$TARGET"
  echo "rendered: $TARGET_REL"
fi
