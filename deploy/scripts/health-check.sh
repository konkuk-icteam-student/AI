#!/usr/bin/env bash
set -euo pipefail

health_url="${1:?usage: health-check.sh <health-url> [attempts]}"
attempts="${2:-30}"

for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if curl --fail --silent --show-error --max-time 10 "${health_url}" >/dev/null; then
        echo "Health check passed: ${health_url}"
        exit 0
    fi

    echo "Health check ${attempt}/${attempts} failed; retrying in 10 seconds"
    sleep 10
done

echo "Health check failed: ${health_url}" >&2
exit 1
