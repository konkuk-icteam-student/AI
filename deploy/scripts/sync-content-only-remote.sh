#!/usr/bin/env bash
# Lightweight Staging checkout sync. NEVER pull/build/restart Docker containers.
set -euo pipefail

requested_sha="$1"
repository_url="$2"
deploy_root="$3"
checkout="$HOME/chatbot/staging"

if [[ ! "$requested_sha" =~ ^[a-f0-9]{40}$ ]]; then
    echo "Expected a 40-character commit SHA" >&2
    exit 2
fi
if [[ ! -d "$checkout/.git" || -L "$checkout" ]]; then
    echo "Staging checkout is missing or is a symlink: $checkout" >&2
    exit 1
fi
if [[ ! -f "$deploy_root/staging/.current-image" ]]; then
    echo "No successful staging image marker exists; refusing source-only sync" >&2
    exit 1
fi

# Share the deployment lock so checkout changes cannot race Docker rollouts.
exec 9>"$deploy_root/.image-deploy.lock"
flock -x 9

if [[ "$(git -C "$checkout" branch --show-current)" != "develop" ||
      "$(git -C "$checkout" remote get-url origin)" != "$repository_url" ||
      "$(git -C "$checkout" rev-parse --show-toplevel)" != "$(cd "$checkout" && pwd -P)" ]]; then
    echo "Staging Git branch/origin/path mismatch" >&2
    exit 1
fi
if ! git -C "$checkout" diff --quiet || ! git -C "$checkout" diff --cached --quiet; then
    echo "Tracked local changes detected; refusing to overwrite" >&2
    exit 1
fi

deployed_sha="$(tr -d '[:space:]' < "$deploy_root/staging/.current-image")"
if [[ ! "$deployed_sha" =~ ^[a-f0-9]{40}$ ]]; then
    echo "Invalid deployed image SHA" >&2
    exit 1
fi

git -C "$checkout" fetch --no-tags origin develop
git -C "$checkout" cat-file -e "$deployed_sha^{commit}"
git -C "$checkout" cat-file -e "$requested_sha^{commit}"

if ! git -C "$checkout" merge-base --is-ancestor "$deployed_sha" "$requested_sha"; then
    echo "Requested commit does not follow the active deployed image" >&2
    exit 1
fi

# Compare against DEPLOYED IMAGE, not just the latest push. A prior code commit
# still being deployed must never be bypassed by a later documentation push.
content_count=0
while IFS= read -r -d '' changed_path; do
    case "$changed_path" in
        *.md|rag_evaluation/questions/*.csv)
            content_count=$((content_count + 1))
            ;;
        *)
            echo "Docker rollout required: $changed_path differs from active image" >&2
            exit 1
            ;;
    esac
done < <(git -C "$checkout" diff --name-only -z "$deployed_sha" "$requested_sha")

if (( content_count == 0 )); then
    echo "No changed documentation/question CSV files; nothing to sync"
    exit 0
fi

# An older queued workflow must not roll the checkout backwards.
if git -C "$checkout" merge-base --is-ancestor "$requested_sha" HEAD; then
    echo "Staging checkout already contains this commit or a newer commit"
    exit 0
fi
if ! git -C "$checkout" merge-base --is-ancestor HEAD "$requested_sha"; then
    echo "Staging checkout cannot fast-forward to requested commit" >&2
    exit 1
fi

git -C "$checkout" merge --ff-only "$requested_sha"
echo "Content-only sync complete: $requested_sha; Docker untouched"
