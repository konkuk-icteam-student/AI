#!/usr/bin/env bash
# Remove only old, unreferenced SHA-tagged images from the deployed AI image repository.
# Default is dry-run. Use --apply only during a serialized deployment.
set -euo pipefail

mode="${1:---dry-run}"
if [[ "$mode" != "--dry-run" && "$mode" != "--apply" ]]; then
    echo "Usage: cleanup-images.sh [--dry-run|--apply]" >&2
    exit 2
fi

image_repository="${IMAGE_REPOSITORY:?IMAGE_REPOSITORY must be set}"
deploy_root="${DEPLOY_ROOT:-/opt/chatbot-service}"
if [[ ! "$image_repository" =~ ^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
    echo "Only a fully qualified GHCR image repository is supported." >&2
    exit 2
fi

# Protect production AND staging releases, including one local rollback image each.
declare -A protected_refs=()
protected_count=0
for environment in staging prod; do
    for marker in .current-image .previous-image; do
        path="${deploy_root}/${environment}/${marker}"
        [[ -e "$path" ]] || continue
        tag="$(tr -d '[:space:]' < "$path")"
        if [[ ! "$tag" =~ ^[a-f0-9]{40}$ ]]; then
            echo "Invalid release marker ${path}; refusing cleanup." >&2
            exit 1
        fi
        protected_refs["${image_repository}:${tag}"]=1
        protected_count=$((protected_count + 1))
    done
done

# No reliable release metadata: fail closed rather than remove possible rollback images.
if (( protected_count == 0 )); then
    echo "No recorded releases; skipping image cleanup."
    exit 0
fi

# Docker daemon failures must never be interpreted as an empty image/container list.
images="$(docker image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}')"
containers="$(docker ps --all --quiet)"
declare -A used_ids=()
while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    image_id="$(docker container inspect --format '{{.Image}}' "$container_id")"
    used_ids["$image_id"]=1
done <<< "$containers"

# Also protect IDs of recorded images if several tags refer to the same image.
for ref in "${!protected_refs[@]}"; do
    if id="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null)"; then
        used_ids["$id"]=1
    fi
done

removed=0
failed=0
while read -r ref image_id; do
    [[ -n "${ref:-}" ]] || continue
    [[ "$ref" == "${image_repository}:"* ]] || continue
    tag="${ref#${image_repository}:}"
    # Do not touch local/latest/untagged, or images from other projects.
    [[ "$tag" =~ ^[a-f0-9]{40}$ ]] || continue
    if [[ -n "${protected_refs[$ref]:-}" || -n "${used_ids[$image_id]:-}" ]]; then
        echo "KEEP ${ref} (active or retained)"
        continue
    fi

    if [[ "$mode" == "--dry-run" ]]; then
        echo "WOULD REMOVE ${ref}"
    elif docker image rm "$ref"; then
        echo "REMOVED ${ref}"
        removed=$((removed + 1))
    else
        echo "WARNING: Failed to remove ${ref}" >&2
        failed=1
    fi
done <<< "$images"

echo "AI image cleanup: mode=${mode}, removed=${removed}, failures=${failed}"
exit "$failed"
