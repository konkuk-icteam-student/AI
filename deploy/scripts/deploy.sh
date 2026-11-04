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
    echo "Created ${env_file}. Set DATABASE_URL and OLLAMA_BASE_URL before deployment." >&2
    exit 1
fi

set -a
# This file is managed by the server administrator.
# shellcheck disable=SC1090
source "${env_file}"
set +a

if [[ "${environment}" == staging ]]; then
    api_port="${API_PORT:-18000}"
else
    api_port="${API_PORT:-8000}"
fi
# Production Compose also creates an isolated PostgreSQL database.
# Fail before pulling/restarting anything when the server's private .env is incomplete.
required_variables=(DATABASE_URL OLLAMA_BASE_URL)
if [[ "${environment}" == prod ]]; then
    required_variables+=(POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD)
fi
for variable in "${required_variables[@]}"; do
    if [[ -z "${!variable:-}" ]]; then
        echo "Missing required configuration: ${variable}" >&2
        exit 1
    fi
done
if [[ ! "${api_port}" =~ ^[0-9]{1,5}$ ]] || (( 10#${api_port} < 1 || 10#${api_port} > 65535 )); then
    echo "API_PORT must be a valid TCP port." >&2
    exit 1
fi
if [[ "${API_BIND_ADDRESS:-127.0.0.1}" != 127.0.0.1 ]]; then
    echo "This shared-server deployment binds the AI API to 127.0.0.1." >&2
    exit 1
fi

ollama_url="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
# host.docker.internal resolves inside Docker via extra_hosts, but typically not
# in the host shell running deployment preflight. Check local Ollama on the host.
ollama_preflight_url="${ollama_url}"
if [[ "${ollama_url}" == "http://host.docker.internal:11434" ]]; then
    ollama_preflight_url="http://127.0.0.1:11434"
fi
curl --fail --silent --show-error --max-time 10 "${ollama_preflight_url}/api/tags" > /dev/null
available_models="$(OLLAMA_HOST="${ollama_preflight_url}" ollama list)"
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
    # The API stays inside the Docker network. The host has no published API port.
    # Compose --wait already waits for the container HEALTHCHECK to become healthy.
    local health_status
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")" || return 1
    if [[ "${health_status}" != healthy ]]; then
        echo "API container health check failed (status: ${health_status})." >&2
        return 1
    fi
    echo "Health check passed: ${environment} API container ${container_id}"
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
    if [[ -n "${previous_image}" && "${previous_image}" != "${image_tag}" ]]; then
        printf '%s\n' "${previous_image}" > "${runtime_dir}/.previous-image"
    fi
    echo "Deployment completed: ${environment} ${image_tag}"
    # Cleanup is best-effort; never fail a healthy deployment due to pruning.
    if ! IMAGE_REPOSITORY="${image_repository}" DEPLOY_ROOT="${deploy_root}" \
        bash "${repository_root}/deploy/scripts/cleanup-images.sh" --apply; then
        echo "WARNING: AI image cleanup failed; deployment remains successful." >&2
    fi
else
    echo "Deployment failed: ${environment} ${image_tag}" >&2
    rollback || true
    exit 1
fi
