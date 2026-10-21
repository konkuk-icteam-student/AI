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

umask 077
mkdir -p "${runtime_dir}" "${storage_dir}/text_pdf" "${storage_dir}/image_pdf" \
    "${config_dir}/prompts" "${config_dir}/search"
chmod 755 "${storage_dir}" "${storage_dir}/text_pdf" "${storage_dir}/image_pdf" \
    "${config_dir}/prompts" "${config_dir}/search"

if [[ ! -f "${env_file}" ]]; then
    cp "${repository_root}/deploy/${environment}/.env.example" "${env_file}"
    chmod 600 "${env_file}"
    echo "Created ${env_file}. Set a secure POSTGRES_PASSWORD and run deployment again." >&2
    exit 1
fi

set -a
# This file is managed by the server administrator.
# shellcheck disable=SC1090
source "${env_file}"
set +a

if [[ "${environment}" == staging ]]; then
    api_port="${API_PORT:-18000}"
    postgres_port="${POSTGRES_PORT:-15433}"
else
    api_port="${API_PORT:-8000}"
    postgres_port="${POSTGRES_PORT:-25433}"
fi
if [[ ! "${POSTGRES_PASSWORD:-}" =~ ^[A-Za-z0-9]{32,}$ ]]; then
    echo "Use a long alphanumeric POSTGRES_PASSWORD; prepare-env.sh generates a URL-safe password." >&2
    exit 1
fi
for port in "${api_port}" "${postgres_port}"; do
    if [[ ! "${port}" =~ ^[0-9]{1,5}$ ]] || (( 10#${port} < 1 || 10#${port} > 65535 )); then
        echo "API_PORT and POSTGRES_PORT must be valid TCP ports." >&2
        exit 1
    fi
done
if [[ "${API_BIND_ADDRESS:-127.0.0.1}" != 127.0.0.1 ]]; then
    echo "This shared-server deployment binds the AI API to 127.0.0.1." >&2
    exit 1
fi

ollama_url="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
curl --fail --silent --show-error --max-time 10 "${ollama_url}/api/tags" > /dev/null
available_models="$(OLLAMA_HOST="${ollama_url}" ollama list)"
for required_model in "${EMBEDDING_MODEL:-qwen3-embedding:0.6b}" "${LLM_MODEL:-qwen3:8b}"; do
    model_found=false
    while read -r model_name rest; do
        if [[ "${model_name}" == "${required_model}" ]]; then
            model_found=true
            break
        fi
    done <<< "${available_models}"
    if [[ "${model_found}" != true ]]; then
        echo "Required Ollama model is not installed: ${required_model}" >&2
        exit 1
    fi
done

install -m 644 "${repository_root}/deploy/${environment}/compose.yaml" "${compose_file}"
install -m 644 "${repository_root}/resources/prompts/chat_answer.txt" "${config_dir}/prompts/chat_answer.txt"
install -m 644 "${repository_root}/resources/search/query_expansions.json" "${config_dir}/search/query_expansions.json"

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

verify_deployment() {
    local tag="$1" container_id actual_image
    container_id="$(compose "${tag}" ps -q api)" || return 1
    if [[ -z "${container_id}" ]]; then
        echo "No API container was created." >&2
        return 1
    fi
    actual_image="$(docker inspect --format '{{.Config.Image}}' "${container_id}")" || return 1
    if [[ "${actual_image}" != "${image_repository}:${tag}" ]]; then
        echo "The running API image does not match the requested release." >&2
        return 1
    fi
    bash "${repository_root}/deploy/scripts/health-check.sh" "http://127.0.0.1:${api_port}/health" 30
}

rollback() {
    if [[ -z "${previous_image}" || "${previous_image}" == "${image_tag}" ]]; then
        echo "No previous image is available for rollback." >&2
        return 1
    fi

    echo "Rolling back from ${image_tag} to ${previous_image}"
    compose "${previous_image}" up -d --wait --wait-timeout 180 --remove-orphans || return 1
    verify_deployment "${previous_image}" || return 1
    write_deployment_env "${previous_image}"
}

echo "Deploying ${image_repository}:${image_tag} to ${environment}"
if compose "${image_tag}" pull \
    && compose "${image_tag}" up -d --wait --wait-timeout 180 --remove-orphans \
    && verify_deployment "${image_tag}"; then
    printf '%s\n' "${image_tag}" > "${current_image_file}"
    write_deployment_env "${image_tag}"
    echo "Deployment completed: ${environment} ${image_tag}"
else
    echo "Deployment failed: ${environment} ${image_tag}" >&2
    rollback || true
    exit 1
fi
