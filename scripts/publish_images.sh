#!/usr/bin/env bash
# Build the project images for linux/amd64 and linux/arm64 and push them to
# DockerHub as one multi-architecture tag each, so the same image name runs on
# an Apple Silicon Mac (arm64) and on a Linux server, VM or Kubernetes node
# (usually amd64). Every host pulls only the variant for its own CPU.
#
# From the repository root:
#   docker login                                  # as $DOCKERHUB_USER
#   scripts/publish_images.sh                     # build both platforms + push
#   scripts/publish_images.sh --check             # build both platforms, no push
#   IMAGE_TAG=0.1.2 scripts/publish_images.sh api-inference frontend
#
# Settings: DOCKERHUB_USER and IMAGE_TAG come from .env (a value exported in
# the shell overrides it for one run); PLATFORMS and BUILDER from the shell.
# After publishing a new tag, set it as IMAGE_TAG in .env so compose and
# Kubernetes (infra/k8s/scripts/render-env.sh) deploy it.
# The images are built from the working tree, so pushing a tree with
# uncommitted changes is refused unless ALLOW_DIRTY=1: the published image must
# match a commit of the repository.
#
# Written for bash 3.2 (the one macOS ships) as well as Linux bash.
set -euo pipefail

cd "$(dirname "$0")/.."

env_value() {
    # Value of $1 from .env, the same file docker compose reads (optional
    # surrounding quotes removed).
    [[ -f .env ]] || return 0
    sed -n "s/^$1=//p" .env | tail -n 1 | sed -e "s/^['\"]//" -e "s/['\"]$//"
}

DOCKERHUB_USER="${DOCKERHUB_USER:-$(env_value DOCKERHUB_USER)}"
IMAGE_TAG="${IMAGE_TAG:-$(env_value IMAGE_TAG)}"
if [[ -z "$DOCKERHUB_USER" || -z "$IMAGE_TAG" ]]; then
    echo "DOCKERHUB_USER and IMAGE_TAG must be set in .env (cp .env.example .env)" >&2
    exit 1
fi
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER="${BUILDER:-diabetes-multiarch}"
ALL_COMPONENTS="api-inference frontend airflow"

dockerfile_for() {
    # Build context is always the repository root.
    case "$1" in
        api-inference) echo apps/api-inference/Dockerfile ;;
        frontend) echo apps/frontend/Dockerfile ;;
        airflow) echo apps/airflow/Dockerfile ;;
        *) return 1 ;;
    esac
}

mode=push
components=""
for arg in "$@"; do
    case "$arg" in
        --check) mode=check ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *)
            dockerfile_for "$arg" >/dev/null \
                || { echo "unknown component: $arg (use: $ALL_COMPONENTS)" >&2; exit 2; }
            components="$components $arg"
            ;;
    esac
done
components="${components:-$ALL_COMPONENTS}"

dirty="$(git status --porcelain)"
if [[ "$mode" == push && -n "$dirty" && "${ALLOW_DIRTY:-0}" != 1 ]]; then
    echo "refusing to push: the working tree has uncommitted changes." >&2
    echo "commit first, or set ALLOW_DIRTY=1 to publish this tree anyway." >&2
    exit 1
fi
revision="$(git rev-parse --short HEAD)"
[[ -z "$dirty" ]] || revision="${revision}-dirty"

# The default `docker` driver cannot build several platforms at once; a
# docker-container builder can, emulating the foreign CPU with QEMU/Rosetta.
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi
docker buildx inspect --bootstrap "$BUILDER" >/dev/null

push_flag=""
[[ "$mode" == push ]] && push_flag="--push"

for component in $components; do
    image="${DOCKERHUB_USER}/diabetes-${component}:${IMAGE_TAG}"
    echo "==> ${image}  [${PLATFORMS}]  (${mode}, git ${revision})"
    # $push_flag is intentionally unquoted: empty means "build only".
    docker buildx build \
        --builder "$BUILDER" \
        --platform "$PLATFORMS" \
        --file "$(dockerfile_for "$component")" \
        --tag "$image" \
        --label "org.opencontainers.image.revision=${revision}" \
        --label "org.opencontainers.image.source=https://github.com/aristotekean/diabetes-mlops" \
        $push_flag \
        .
    if [[ "$mode" == push ]]; then
        docker buildx imagetools inspect "$image" | grep -E '^Name:|Platform: +linux/'
    fi
done
