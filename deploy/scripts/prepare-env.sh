#!/usr/bin/env bash
set -euo pipefail

environment="${1:?usage: prepare-env.sh <staging|prod>}"
deploy_root="${DEPLOY_ROOT:-/opt/chatbot-service}"
case "${environment}" in
    staging) api_port=18000; postgres_port=15433 ;;
    prod) api_port=8000; postgres_port=25433 ;;
    *) echo "environment must be staging or prod" >&2; exit 1 ;;
esac

if [[ ! -d "${deploy_root}" || ! -w "${deploy_root}" ]]; then
    echo "The deployment user must own a writable ${deploy_root}." >&2
    exit 1
fi

umask 077
runtime_dir="${deploy_root}/${environment}"
documents_dir="${deploy_root}/storage/${environment}/documents"
mkdir -p "${runtime_dir}" "${documents_dir}/text_pdf" "${documents_dir}/image_pdf"
chmod 755 "${documents_dir}" "${documents_dir}/text_pdf" "${documents_dir}/image_pdf"

env_file="${runtime_dir}/.env"
if [[ -e "${env_file}" ]]; then
    echo "Environment file already exists; it was not overwritten: ${env_file}"
    exit 0
fi

postgres_password="$(openssl rand -hex 32)"
# A noclobber subshell also protects against another preparation process.
(
    set -o noclobber
    cat > "${env_file}" <<EOF
POSTGRES_DB=chatbot
POSTGRES_PASSWORD=${postgres_password}
POSTGRES_PORT=${postgres_port}
API_BIND_ADDRESS=127.0.0.1
API_PORT=${api_port}
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=qwen3-embedding:0.6b
LLM_MODEL=qwen3:8b
EOF
)
chmod 600 "${env_file}"
unset postgres_password
echo "Environment prepared: ${env_file}"
echo "Loopback API port: ${api_port}; loopback database port: ${postgres_port}"
