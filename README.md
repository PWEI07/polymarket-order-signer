# Polymarket Order-Only Signer

A turnkey Docker service that signs Polymarket CLOB orders on behalf of a user. It **cannot** sign withdrawals, transfers, or any other on-chain operations — only trade orders.

用户 clone 这个项目后，填 3 个值，跑一个命令完成设置，再启动签名服务即可。交易执行方可以通过 API 请求签单，但无法提取资金。

**Shippable copy:** use the top-level folder `polymarket-order-signer/` in this repository. The old path `tools/polymarket_order_signer/` only redirects here.
## Quick Start

```bash
git clone <this-repo>
cd polymarket-order-signer

# 1. Configure — fill in 3 values
cp .env.example .env
#    Edit .env: PRIVATE_KEY, RELAY_API_KEY, RELAY_API_KEY_ADDRESS

# 2. One-time setup — deploys wallet, approves contracts, generates CLOB creds
docker compose run --rm setup

# 3. Start the signer
docker compose up -d signer
```

## Prerequisites / 前置条件

You need 3 things, all from the same Polymarket account:

| Value | Where to find it |
|-------|-----------------|
| `PRIVATE_KEY` | MetaMask/Rabby: Account Details → Export Private Key. Format: `0x` + 64 hex chars. |
| `RELAY_API_KEY` | [polymarket.com/settings](https://polymarket.com/settings) → API Keys tab |
| `RELAY_API_KEY_ADDRESS` | Same page — the EOA address that owns the key |

如果用邮箱/Google 注册 Polymarket，通常无法导出 private key。建议新建一个 MetaMask 钱包连接 Polymarket。不要使用主钱包，建议只放少量资金。

## What `setup` Does

The setup command runs 4 automatic steps:

1. **Deploy deposit wallet** — creates an on-chain ERC-1967 proxy via the Polymarket Relayer
2. **Approve trading contracts** — submits 6 approval transactions (pUSD + CTF tokens for both exchanges)
3. **Generate CLOB credentials** — derives API key/secret/passphrase from your private key
4. **Verify** — confirms everything is deployed and configured

All values are written back to your `.env` file. The script is idempotent — safe to re-run.

After setup, fund your deposit wallet (address printed at the end) with pUSD to start trading.

## Deploy to Cloud (Recommended) / 一键云部署

Running the signer on your own computer means unstable IPs and downtime. Deploy to **Fly.io** for a stable `https://` URL with zero maintenance. Your private key is stored as an **encrypted secret** in your own Fly.io account — the trading operator never sees it.

在自己电脑上跑 signer 会有 IP 变化和宕机问题。推荐部署到 Fly.io，获得稳定的 https 地址。你的私钥作为加密密钥存在你自己的 Fly.io 账户里，交易执行方看不到。

### Fly.io (~$2/month, auto-sleeps when idle)

```bash
# 1. Install Fly CLI (one-time)
curl -L https://fly.io/install.sh | sh

# 2. Sign up / log in
fly auth signup       # first time
# fly auth login      # returning user

# 3. Launch app (from this directory, after `docker compose run --rm setup`)
fly launch --no-deploy

# 4. Upload secrets (encrypted — only you can see them)
fly secrets set \
  PRIVATE_KEY="0xYOUR_PRIVATE_KEY" \
  RELAY_API_KEY="your-relay-api-key" \
  RELAY_API_KEY_ADDRESS="0xYOUR_EOA_ADDRESS" \
  DEPOSIT_WALLET="0xYOUR_DEPOSIT_WALLET" \
  CLOB_API_KEY="from-your-.env" \
  CLOB_API_SECRET="from-your-.env" \
  CLOB_API_PASSPHRASE="from-your-.env" \
  ORDER_SIGNER_AUTH_TOKEN="pick-a-long-random-string"

# 5. Deploy
fly deploy

# 6. Verify
fly status                         # shows your app URL
curl https://YOUR-APP.fly.dev/health   # should return {"ok": true, ...}
```

After deploy, your signer URL is `https://YOUR-APP.fly.dev`. Share this with your trading operator along with `ORDER_SIGNER_AUTH_TOKEN` and `RELAY_API_KEY_ADDRESS`.

部署后你的 signer 地址就是 `https://YOUR-APP.fly.dev`。把这个地址、`ORDER_SIGNER_AUTH_TOKEN` 和 `RELAY_API_KEY_ADDRESS` 分享给交易执行方即可。

### Alternative: Railway (free tier, $1/month credit)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Railway is free but limited to $1/month of compute. The signer is lightweight enough to fit. After deploy, Railway gives you a stable URL in the dashboard.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Status check |
| `/sign-order` | POST | Signs a CLOB order (requires Bearer token) |
| `/sign-wallet-batch` | POST | **403** — always rejected |
| `/sign-message` | POST | **403** — always rejected |
| `/submit-transaction` | POST | **403** — always rejected |

### Sign an order

```bash
curl -X POST http://localhost:8080/sign-order \
  -H "Authorization: Bearer $ORDER_SIGNER_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"token_id":"TOKEN_ID","side":"BUY","price":"0.50","size":"10","tick_size":"0.01"}'
```

## What to Share / 信息分配

| Share with trading operator | Keep private |
|----------------------------|--------------|
| `CLOB_API_KEY` | `PRIVATE_KEY` |
| `CLOB_API_SECRET` | `RELAY_API_KEY` |
| `CLOB_API_PASSPHRASE` | |
| `DEPOSIT_WALLET` | |
| `RELAY_API_KEY_ADDRESS` (your EOA/signer address) | |
| Signer URL + `ORDER_SIGNER_AUTH_TOKEN` | |

The trading operator uses the CLOB credentials for HMAC authentication and calls your signer to get order signatures. They cannot withdraw funds.

交易执行方用 CLOB 三件套做 HTTP 认证，调用你的 signer 签订单。他们无法提现，因为 signer 拒绝签署任何非订单操作。

## Security Boundaries / 安全边界

The signer can:
- Sign CLOB trade orders

The signer cannot:
- Sign withdrawal or transfer transactions
- Sign arbitrary messages
- Sign Relayer WALLET batches (approvals, transfers, redeems)
- Access funds without the user funding the deposit wallet

If someone obtains your `PRIVATE_KEY`, this boundary no longer holds. Keep it on the signer server only.

## Configuration

All settings are in `.env`. See `.env.example` for documentation on each field.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PRIVATE_KEY` | Yes | — | EOA private key |
| `RELAY_API_KEY` | Yes | — | Polymarket Relayer key |
| `RELAY_API_KEY_ADDRESS` | Yes | — | EOA address |
| `ORDER_SIGNER_AUTH_TOKEN` | Recommended | — | Bearer token for API auth |
| `ORDER_SIGNER_ALLOW_ANY_TOKEN` | No | `true` | Allow all markets |
| `MAX_ORDER_NOTIONAL_USD` | No | `10` | Max order value |
| `MAX_ORDER_SIZE` | No | `100` | Max order size |

## Optional: automated smoke (build + setup + health)

From `polymarket-order-signer/` after `.env` exists:

```bash
chmod +x scripts/smoke.sh
./scripts/smoke.sh
```

Stops containers when done (uses `docker compose down` on exit).

## Troubleshooting

**"RELAY_API_KEY_ADDRESS must match the address derived from PRIVATE_KEY"** — The relay key must be tied to the same EOA as your `PRIVATE_KEY`. Usually `RELAY_API_KEY_ADDRESS` equals your MetaMask account address.

**"Missing required values in .env"** — Make sure PRIVATE_KEY, RELAY_API_KEY, and RELAY_API_KEY_ADDRESS are all set.

**"PRIVATE_KEY must be 0x + 64 hex characters"** — Export the correct private key from your wallet. It should look like `0xabc123...` (66 characters total).

**"Relayer WALLET-CREATE failed: 401"** — Your RELAY_API_KEY is invalid or expired. Generate a new one at polymarket.com/settings.

**"not enough balance"** when placing orders — Fund the deposit wallet address (shown in setup output) with pUSD.

**"Trading restricted in your region"** — The signer server must be hosted in a non-restricted region (e.g., Europe). Use a VPS like Hetzner.
