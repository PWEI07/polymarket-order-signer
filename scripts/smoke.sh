#!/usr/bin/env bash
# End-to-end smoke: build image, run setup, start signer, hit /health, stop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and fill PRIVATE_KEY, RELAY_API_KEY, RELAY_API_KEY_ADDRESS" >&2
  exit 1
fi

echo "==> docker compose build"
docker compose build

echo "==> docker compose run setup (one-time / idempotent)"
docker compose run --rm setup

echo "==> docker compose up signer"
docker compose up -d signer
trap 'docker compose down' EXIT

for i in {1..30}; do
  if curl -sf "http://127.0.0.1:8080/health" >/dev/null; then
    echo "==> /health OK"
    curl -s "http://127.0.0.1:8080/health" | head -c 500
    echo
    exit 0
  fi
  sleep 1
done

echo "Signer did not become healthy on :8080" >&2
exit 1
