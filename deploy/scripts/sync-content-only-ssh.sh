#!/usr/bin/env bash
# CI -> staging host. No Docker operations; only a Git fast-forward.
set -euo pipefail

commit_sha="$1"
deploy_root="${DEPLOY_ROOT:-/opt/chatbot-service}"
ssh_port="${DEPLOY_PORT:-22}"

for variable in DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_PRIVATE_KEY DEPLOY_HOST_KEY GITHUB_REPOSITORY; do
    if [[ -z "${!variable:-}" ]]; then
        echo "Missing staging SSH setting: $variable" >&2
        exit 1
    fi
done

if [[ ! "$commit_sha" =~ ^[a-f0-9]{40}$ ]] ||
   [[ ! "$deploy_root" =~ ^/[A-Za-z0-9/_-]+$ || "$deploy_root" == / || "$deploy_root" == *//* ]] ||
   [[ ! "$DEPLOY_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
   [[ ! "$DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] ||
   [[ ! "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
   [[ ! "$ssh_port" =~ ^[0-9]{1,5}$ ]] ||
   (( 10#$ssh_port < 1 || 10#$ssh_port > 65535 )); then
    echo "Invalid staging SSH parameters" >&2
    exit 2
fi
ssh_port="$((10#$ssh_port))"

umask 077
tmpdir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/rag-content-sync.XXXXXX")"
trap 'rm -rf -- "$tmpdir"' EXIT
printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" > "$tmpdir/key"
chmod 600 "$tmpdir/key"

if (( ssh_port == 22 )); then
    printf '%s %s\n' "$DEPLOY_HOST" "$DEPLOY_HOST_KEY" > "$tmpdir/known_hosts"
else
    printf '[%s]:%s %s\n' "$DEPLOY_HOST" "$ssh_port" "$DEPLOY_HOST_KEY" > "$tmpdir/known_hosts"
fi
ssh-keygen -y -P '' -f "$tmpdir/key" >/dev/null
ssh-keygen -l -f "$tmpdir/known_hosts" >/dev/null

ssh_options=(
    -i "$tmpdir/key"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o HostKeyAlgorithms=ssh-ed25519
    -o "UserKnownHostsFile=$tmpdir/known_hosts"
    -o GlobalKnownHostsFile=/dev/null
    -o ConnectTimeout=15
)

repository_url="https://github.com/$GITHUB_REPOSITORY.git"
remote_script="$(cd "$(dirname "$0")" && pwd)/sync-content-only-remote.sh"

ssh "${ssh_options[@]}" -p "$ssh_port" "$DEPLOY_USER@$DEPLOY_HOST" \
    "bash -s -- '$commit_sha' '$repository_url' '$deploy_root'" < "$remote_script"
