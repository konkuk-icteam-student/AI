#!/usr/bin/env bash
set -euo pipefail

environment="${1:?usage: deploy-ssh.sh <staging|prod> <image-repository> <image-tag>}"
image_repository="${2:?image repository is required}"
image_tag="${3:?image tag is required}"
deploy_root="${DEPLOY_ROOT:-/opt/chatbot-service}"
ssh_port="${DEPLOY_PORT:-22}"

for variable in DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_PRIVATE_KEY DEPLOY_HOST_KEY GHCR_USER GHCR_TOKEN; do
    if [[ -z "${!variable:-}" ]]; then
        echo "Missing deployment setting: ${variable}" >&2
        exit 1
    fi
done

# These values also form remote shell arguments and paths; accept safe forms only.
if [[ "${environment}" != staging && "${environment}" != prod ]] \
    || [[ ! "${image_repository}" =~ ^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+$ ]] \
    || [[ ! "${image_tag}" =~ ^[a-f0-9]{40}$ ]] \
    || [[ ! "${deploy_root}" =~ ^/[A-Za-z0-9/_-]+$ ]] \
    || [[ "${deploy_root}" == / || "${deploy_root}" == *//* ]] \
    || [[ ! "${DEPLOY_HOST}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || [[ ! "${DEPLOY_USER}" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] \
    || [[ ! "${GHCR_USER}" =~ ^[A-Za-z0-9_-]+(\[bot\])?$ ]] \
    || [[ ! "${ssh_port}" =~ ^[0-9]{1,5}$ ]] \
    || (( 10#${ssh_port} < 1 || 10#${ssh_port} > 65535 )); then
    echo "Invalid deployment environment, image, path, host, user, or port." >&2
    exit 1
fi
ssh_port="$((10#${ssh_port}))"

repository_root="$(cd "$(dirname "$0")/../.." && pwd)"
umask 077
transport_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/chatbot-ssh.XXXXXX")"
trap 'rm -f "${transport_dir}/id_ed25519" "${transport_dir}/known_hosts" "${transport_dir}/bundle.tar.gz"; rmdir "${transport_dir}"' EXIT
printf '%s\n' "${DEPLOY_SSH_PRIVATE_KEY}" > "${transport_dir}/id_ed25519"
chmod 600 "${transport_dir}/id_ed25519"
ssh-keygen -y -P '' -f "${transport_dir}/id_ed25519" > /dev/null

if (( ssh_port == 22 )); then
    printf '%s %s\n' "${DEPLOY_HOST}" "${DEPLOY_HOST_KEY}" > "${transport_dir}/known_hosts"
else
    printf '[%s]:%s %s\n' "${DEPLOY_HOST}" "${ssh_port}" "${DEPLOY_HOST_KEY}" > "${transport_dir}/known_hosts"
fi
ssh-keygen -l -f "${transport_dir}/known_hosts" > /dev/null

ssh_options=(
    -i "${transport_dir}/id_ed25519"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o HostKeyAlgorithms=ssh-ed25519
    -o "UserKnownHostsFile=${transport_dir}/known_hosts"
    -o GlobalKnownHostsFile=/dev/null
    -o ConnectTimeout=15
)
destination="${DEPLOY_USER}@${DEPLOY_HOST}"
release_dir="${deploy_root}/releases/${environment}/${image_tag}"

# Fail before transferring files if the server environment has not been prepared.
ssh "${ssh_options[@]}" -p "${ssh_port}" "${destination}" \
    "test -s '${deploy_root}/${environment}/.env' && mkdir -p '${release_dir}'" \
    || { echo "SSH preflight failed. Verify connectivity and run prepare-env.sh on the server." >&2; exit 1; }

# Send only deployment/configuration files; exclude PDFs, .env, and credentials.
tar -czf "${transport_dir}/bundle.tar.gz" -C "${repository_root}" \
    deploy/staging/compose.yaml deploy/staging/.env.example \
    deploy/prod/compose.yaml deploy/prod/.env.example \
    deploy/scripts/deploy.sh deploy/scripts/health-check.sh \
    deploy/scripts/deploy-remote.sh \
    resources/prompts/chat_answer.txt resources/search/query_expansions.json
scp "${ssh_options[@]}" -P "${ssh_port}" "${transport_dir}/bundle.tar.gz" \
    "${destination}:${release_dir}/bundle.tar.gz"
ssh "${ssh_options[@]}" -p "${ssh_port}" "${destination}" \
    "tar --no-same-owner -xzf '${release_dir}/bundle.tar.gz' -C '${release_dir}'"

# The token is carried by encrypted stdin, never inserted into a remote command.
printf '%s\n' "${GHCR_TOKEN}" \
    | ssh "${ssh_options[@]}" -p "${ssh_port}" "${destination}" \
        "bash '${release_dir}/deploy/scripts/deploy-remote.sh' '${environment}' '${image_repository}' '${image_tag}' '${deploy_root}' '${GHCR_USER}'"
