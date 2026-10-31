#!/bin/sh
# Static check of infra/k8s, meant as a pre-commit step (this repository has no
# CI): the kustomization must build, and the committed migrations ConfigMap
# must match infra/database (see render-migrations.sh). Client-side only.
#
#   bash infra/k8s/scripts/check.sh
set -eu

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

kubectl kustomize "$ROOT/infra/k8s" > /dev/null
sh "$ROOT/infra/k8s/scripts/render-migrations.sh" --check
echo "infra/k8s: kustomize build OK, migrations ConfigMap up to date"
