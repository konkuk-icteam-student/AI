#!/usr/bin/env bash
# Synchronize a successful deployment's source commit into the deploy user's checkout.
# Runs on the server after the Docker image has passed its health checks.
set -euo pipefail

environment="${1:?usage: sync-checkout.sh <staging|prod> <commit-sha> <repository-url> [checkout-root]}"
commit_sha="${2:?commit SHA is required}"
repository_url="${3:?repository URL is required}"
checkout_root="${4:-${HOME:?}/chatbot}"

case "${environment}" in
    staging) branch=develop ;;
    prod) branch=main ;;
    *) echo "environment must be staging or prod" >&2; exit 2 ;;
esac
if [[ ! "${commit_sha}" =~ ^[a-f0-9]{40}$ ]]; then
    echo "commit SHA must have 40 lowercase hex characters" >&2
    exit 2
fi
if [[ "${checkout_root}" != /* || "${checkout_root}" == / ||
      "${checkout_root}" == *'/../'* || "${checkout_root}" == *'/./'* ||
      "${checkout_root}" == *'//'*
    ]]; then
    echo "checkout root must be a safe absolute path" >&2
    exit 2
fi

target="${checkout_root}/${environment}"
if [[ -L "${checkout_root}" || -L "${target}" ]]; then
    echo "Refusing symlink checkout path: ${target}" >&2
    exit 1
fi
mkdir -p "${checkout_root}"

if [[ ! -d "${target}/.git" ]]; then
    # git clone is safe into a *completely empty* folder. Never overwrite files.
    if [[ -e "${target}" ]] && [[ -n "$(find "${target}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        echo "Refusing nonempty non-Git directory: ${target}" >&2
        exit 1
    fi
    git clone --branch "${branch}" --single-branch "${repository_url}" "${target}"
    # A newer push may have happened during the image build; pin the checkout
    # to exactly the SHA used for the verified Docker deployment.
    git -C "${target}" cat-file -e "${commit_sha}^{commit}"
    git -C "${target}" reset --hard "${commit_sha}"
else
    actual_root="$(git -C "${target}" rev-parse --show-toplevel)"
    if [[ "${actual_root}" != "$(cd "${target}" && pwd -P)" ]]; then
        echo "Refusing nested Git repository: ${target}" >&2
        exit 1
    fi
    current_branch="$(git -C "${target}" symbolic-ref --quiet --short HEAD || true)"
    origin_url="$(git -C "${target}" remote get-url origin || true)"
    if [[ "${current_branch}" != "${branch}" || "${origin_url}" != "${repository_url}" ]]; then
        echo "Checkout branch/origin mismatch in ${target}" >&2
        exit 1
    fi
    if ! git -C "${target}" diff --quiet || ! git -C "${target}" diff --cached --quiet; then
        echo "Tracked local edits detected in ${target}; refusing to overwrite" >&2
        exit 1
    fi
    git -C "${target}" fetch --no-tags origin "${branch}"
    git -C "${target}" cat-file -e "${commit_sha}^{commit}"
    git -C "${target}" merge --ff-only "${commit_sha}"
fi

actual_sha="$(git -C "${target}" rev-parse HEAD)"
if [[ "${actual_sha}" != "${commit_sha}" ]]; then
    echo "Checkout SHA mismatch: requested ${commit_sha}, actual ${actual_sha}" >&2
    exit 1
fi
echo "Synchronized ${branch} (${actual_sha}) to ${target}"
