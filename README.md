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

# 3. Deploy to cloud (one command — handles everything)
./scripts/deploy-fly.sh
#    Prints a ready-to-copy block with all values to send to your trading operator.
```

That's it. Step 3 installs the Fly CLI if needed, signs you up, encrypts your secrets,
deploys the signer, and prints exactly what to share. Your private key never leaves
your Fly.io account.

就这三步。第三步会自动安装 Fly CLI、注册、加密上传密钥、部署签名服务，最后打印一段
可以直接复制发给交易执行方的文本。你的私钥始终只在你自己的 Fly.io 账户里。

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

### One command (after setup)

```bash
./scripts/deploy-fly.sh
```

This script automatically:
1. Installs the Fly CLI if not present
2. Signs you up / logs you in
3. Creates a Fly app
4. Uploads all secrets from your `.env` (encrypted in Fly's vault)
5. Auto-generates `ORDER_SIGNER_AUTH_TOKEN` if you haven't set one
6. Deploys the signer
7. **Prints a ready-to-copy block** with everything your trading operator needs

To redeploy after changes: `./scripts/deploy-fly.sh update`

### Manual steps (if you prefer)

<details>
<summary>Click to expand manual Fly.io instructions</summary>

```bash
curl -L https://fly.io/install.sh | sh
fly auth signup
fly launch --no-deploy
fly secrets set \
  PRIVATE_KEY="..." RELAY_API_KEY="..." RELAY_API_KEY_ADDRESS="..." \
  DEPOSIT_WALLET="..." CLOB_API_KEY="..." CLOB_API_SECRET="..." \
  CLOB_API_PASSPHRASE="..." ORDER_SIGNER_AUTH_TOKEN="..."
fly deploy
fly status
```
</details>

### Alternative: Local Docker

If you prefer to run locally (stable server with static IP required):

```bash
docker compose up -d signer
```

### Alternative: Railway (free tier, $1/month credit)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

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
