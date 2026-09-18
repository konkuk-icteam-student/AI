#!/usr/bin/env bash
set -euo pipefail

environment="${1:?usage: deploy.sh <staging|prod> <image-repository> <image-tag>}"
image_repository="${2:?image repository is required}"
image_tag="${3:?image tag is required}"

if [[ "${environment}" != "staging" && "${environment}" != "prod" ]]; then
    echo "environment must be staging or prod" >&2
    exit 1
fi

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
deploy_root="${DEPLOY_ROOT:-/opt/chatbot-service}"
runtime_dir="${deploy_root}/${environment}"
storage_dir="${deploy_root}/storage/${environment}/documents"
config_dir="${deploy_root}/config/${environment}"
env_file="${runtime_dir}/.env"
compose_file="${runtime_dir}/compose.yaml"
current_image_file="${runtime_dir}/.current-image"
deployment_env_file="${runtime_dir}/.deployment.env"

mkdir -p "${runtime_dir}" "${storage_dir}" "${config_dir}/prompts" "${config_dir}/search"

if [[ ! -f "${env_file}" ]]; then
    cp "${repository_root}/deploy/${environment}/.env.example" "${env_file}"
    echo "Created ${env_file}. Set a secure POSTGRES_PASSWORD and run deployment again." >&2
    exit 1
fi

cp "${repository_root}/deploy/${environment}/compose.yaml" "${compose_file}"
cp "${repository_root}/resources/prompts/chat_answer.txt" "${config_dir}/prompts/chat_answer.txt"
cp "${repository_root}/resources/search/query_expansions.json" "${config_dir}/search/query_expansions.json"

set -a
# This file is managed by the server administrator.
# shellcheck disable=SC1090
source "${env_file}"
set +a

api_port="${API_PORT:-8000}"
if [[ "${POSTGRES_PASSWORD:-}" == "replace-with-a-long-random-password" ]]; then
    echo "Replace the example POSTGRES_PASSWORD in ${env_file} before deployment." >&2
    exit 1
fi

previous_image=""
if [[ -f "${current_image_file}" ]]; then
    previous_image="$(tr -d '[:space:]' < "${current_image_file}")"
fi

compose() {
    IMAGE_REPOSITORY="${image_repository}" \
    IMAGE_TAG="$1" \
    DOCUMENTS_PATH="${storage_dir}" \
    PROMPTS_PATH="${config_dir}/prompts" \
    SEARCH_CONFIG_PATH="${config_dir}/search" \
        docker compose --env-file "${env_file}" -f "${compose_file}" "${@:2}"
}

write_deployment_env() {
    cat > "${deployment_env_file}" <<EOF
IMAGE_REPOSITORY=${image_repository}
IMAGE_TAG=$1
DOCUMENTS_PATH=${storage_dir}
PROMPTS_PATH=${config_dir}/prompts
SEARCH_CONFIG_PATH=${config_dir}/search
EOF
}

rollback() {
    if [[ -z "${previous_image}" || "${previous_image}" == "${image_tag}" ]]; then
        echo "No previous image is available for rollback." >&2
        return 1
    fi

    echo "Rolling back from ${image_tag} to ${previous_image}"
    compose "${previous_image}" up -d --remove-orphans
    "${repository_root}/deploy/scripts/health-check.sh" "http://127.0.0.1:${api_port}/health" 18
    write_deployment_env "${previous_image}"
}

echo "Deploying ${image_repository}:${image_tag} to ${environment}"
if compose "${image_tag}" pull \
    && compose "${image_tag}" up -d --remove-orphans \
    && "${repository_root}/deploy/scripts/health-check.sh" "http://127.0.0.1:${api_port}/health" 30; then
    printf '%s\n' "${image_tag}" > "${current_image_file}"
    write_deployment_env "${image_tag}"
    echo "Deployment completed: ${environment} ${image_tag}"
else
    echo "Deployment failed: ${environment} ${image_tag}" >&2
    rollback || true
    exit 1
fi
