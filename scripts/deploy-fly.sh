#!/usr/bin/env bash
# One-command Fly.io deployment for polymarket-order-signer.
# Reads .env (already populated by setup.py), deploys to Fly.io,
# and prints a ready-to-copy block to send to your trading operator.
#
# Usage:
#   ./scripts/deploy-fly.sh          # first deploy
#   ./scripts/deploy-fly.sh update   # redeploy after changes
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── Helpers ───────────────────────────────────────────────────────────────────

die() { echo -e "\n  ERROR: $1\n" >&2; exit 1; }

env_val() {
  # Read a value from .env, stripping quotes and whitespace.
  grep -E "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']\|["'\''"]$//g' | xargs
}

rand_hex() { python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32; }

# ── Pre-flight checks ────────────────────────────────────────────────────────

[[ -f .env ]] || die "No .env found.\nRun setup first:\n  cp .env.example .env\n  # fill PRIVATE_KEY, RELAY_API_KEY, RELAY_API_KEY_ADDRESS\n  docker compose run --rm setup"

for key in PRIVATE_KEY RELAY_API_KEY RELAY_API_KEY_ADDRESS DEPOSIT_WALLET CLOB_API_KEY CLOB_API_SECRET CLOB_API_PASSPHRASE; do
  val="$(env_val "$key")"
  [[ -n "$val" && "$val" != *"_here"* && "$val" != *"your-"* ]] || die "Missing or placeholder value for $key in .env.\nDid you run 'docker compose run --rm setup' first?"
done

# ── Auto-generate ORDER_SIGNER_AUTH_TOKEN if not set ─────────────────────────

AUTH_TOKEN="$(env_val ORDER_SIGNER_AUTH_TOKEN)"
if [[ -z "$AUTH_TOKEN" || "$AUTH_TOKEN" == "change-me-to-a-random-secret" ]]; then
  AUTH_TOKEN="$(rand_hex)"
  echo "  -> Generated ORDER_SIGNER_AUTH_TOKEN (random 64-char hex)"
  if grep -q "^ORDER_SIGNER_AUTH_TOKEN=" .env; then
    sed -i.bak "s|^ORDER_SIGNER_AUTH_TOKEN=.*|ORDER_SIGNER_AUTH_TOKEN=${AUTH_TOKEN}|" .env && rm -f .env.bak
  else
    echo "ORDER_SIGNER_AUTH_TOKEN=${AUTH_TOKEN}" >> .env
  fi
fi

# ── Install flyctl if needed ─────────────────────────────────────────────────

if ! command -v fly &>/dev/null && ! command -v flyctl &>/dev/null; then
  echo "==> Installing Fly CLI..."
  curl -L https://fly.io/install.sh | sh
  export PATH="$HOME/.fly/bin:$PATH"
fi

FLY="$(command -v fly 2>/dev/null || command -v flyctl 2>/dev/null)"
echo "==> Using Fly CLI: $FLY"

# ── Authenticate if needed ───────────────────────────────────────────────────

if ! "$FLY" auth whoami &>/dev/null; then
  echo "==> Not logged in. Opening Fly.io signup/login..."
  "$FLY" auth login
fi
echo "==> Authenticated as: $("$FLY" auth whoami 2>/dev/null || echo '(unknown)')"

# ── Launch or update ─────────────────────────────────────────────────────────

if [[ "${1:-}" == "update" ]]; then
  echo "==> Redeploying existing app..."
else
  if [[ -f fly.toml ]] && "$FLY" status &>/dev/null; then
    echo "==> App already exists, redeploying..."
  else
    echo "==> Launching new Fly app (you can accept defaults)..."
    "$FLY" launch --no-deploy --copy-config
  fi
fi

# ── Push secrets from .env ───────────────────────────────────────────────────

echo "==> Uploading encrypted secrets to Fly.io..."
"$FLY" secrets set \
  PRIVATE_KEY="$(env_val PRIVATE_KEY)" \
  RELAY_API_KEY="$(env_val RELAY_API_KEY)" \
  RELAY_API_KEY_ADDRESS="$(env_val RELAY_API_KEY_ADDRESS)" \
  DEPOSIT_WALLET="$(env_val DEPOSIT_WALLET)" \
  CLOB_API_KEY="$(env_val CLOB_API_KEY)" \
  CLOB_API_SECRET="$(env_val CLOB_API_SECRET)" \
  CLOB_API_PASSPHRASE="$(env_val CLOB_API_PASSPHRASE)" \
  ORDER_SIGNER_AUTH_TOKEN="$AUTH_TOKEN" \
  SIGNATURE_TYPE="$(env_val SIGNATURE_TYPE || echo 3)"

# ── Deploy ───────────────────────────────────────────────────────────────────

echo "==> Deploying..."
"$FLY" deploy

# ── Get app URL ──────────────────────────────────────────────────────────────

APP_NAME="$("$FLY" status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['Name'])" 2>/dev/null || echo "YOUR-APP")"
SIGNER_URL="https://${APP_NAME}.fly.dev"

# ── Health check ─────────────────────────────────────────────────────────────

echo "==> Waiting for signer to become healthy..."
for i in $(seq 1 30); do
  if curl -sf "${SIGNER_URL}/health" >/dev/null 2>&1; then
    echo "  -> /health OK"
    break
  fi
  sleep 2
done

# ── Print share block ────────────────────────────────────────────────────────

cat <<SHARE

════════════════════════════════════════════════════════════════
  DEPLOYMENT COMPLETE — Copy everything below and send to
  your trading operator:
════════════════════════════════════════════════════════════════

DEPOSIT_WALLET=$(env_val DEPOSIT_WALLET)
CLOB_API_KEY=$(env_val CLOB_API_KEY)
CLOB_API_SECRET=$(env_val CLOB_API_SECRET)
CLOB_API_PASSPHRASE=$(env_val CLOB_API_PASSPHRASE)
RELAY_API_KEY_ADDRESS=$(env_val RELAY_API_KEY_ADDRESS)
SIGNER_URL=${SIGNER_URL}
ORDER_SIGNER_AUTH_TOKEN=${AUTH_TOKEN}

════════════════════════════════════════════════════════════════
  DO NOT share: PRIVATE_KEY, RELAY_API_KEY
  Fund your deposit wallet with pUSD to start trading.
════════════════════════════════════════════════════════════════
SHARE
