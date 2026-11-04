#!/usr/bin/env bash
# Invoked by deploy-ssh.sh; the short-lived registry token arrives on stdin.
set -euo pipefail

environment="${1:?environment is required}"
image_repository="${2:?image repository is required}"
image_tag="${3:?image tag is required}"
export DEPLOY_ROOT="${4:?deploy root is required}"
ghcr_user="${5:?registry user is required}"

if [[ ! -s "${DEPLOY_ROOT}/${environment}/.env" ]]; then
    echo "Prepare the server environment with prepare-env.sh before deployment." >&2
    exit 1
fi

umask 077
export DOCKER_CONFIG
DOCKER_CONFIG="$(mktemp -d "${DEPLOY_ROOT}/.registry-auth.XXXXXX")"
trap 'rm -rf -- "${DOCKER_CONFIG}"' EXIT
IFS= read -r registry_token
if [[ -z "${registry_token}" ]]; then
    echo "Registry token is missing." >&2
    exit 1
fi
printf '%s\n' "${registry_token}" \
    | docker login ghcr.io -u "${ghcr_user}" --password-stdin
unset registry_token

# Serialize pulls, container swaps and cleanup across staging and production.
# This prevents one deployment from deleting an image another is pulling.
exec 9>"${DEPLOY_ROOT}/.image-deploy.lock"
flock -x 9
bash "$(dirname "$0")/deploy.sh" "${environment}" "${image_repository}" "${image_tag}"

# Sync a full source checkout for batch RAG questions only after the API deploy
# succeeded. No env files, Docker data, or local result files are deleted.
if [[ ! "${image_repository}" =~ ^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
    echo "Invalid image repository for source checkout" >&2
    exit 1
fi
repository_url="https://github.com/${image_repository#ghcr.io/}.git"
bash "$(dirname "$0")/sync-checkout.sh" "${environment}" "${image_tag}" "${repository_url}"
