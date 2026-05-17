# Polymarket Order-Only Signer

这个工具帮你在 Fly.io 上部署一个签名服务，让交易执行方可以代你下单交易，但**无法提现或转账**。你的私钥加密存储在你自己的 Fly.io 账户里，交易执行方看不到。

This tool deploys a signing service to Fly.io so your trading operator can place orders on your behalf, but **cannot withdraw or transfer funds**. Your private key is encrypted in your own Fly.io account — the operator never sees it.

## How It Works / 原理

1. You run a one-time setup that creates a trading wallet and API credentials on Polymarket
2. The deploy script packages everything into a signer service on Fly.io (stable `https://` URL, always online)
3. You send the printed output to your trading operator — they can sign orders through your signer, nothing else

## What You Need / 你需要准备

| What | Where to get it |
|------|----------------|
| `PRIVATE_KEY` | MetaMask/Rabby: Account Details → Export Private Key. Format: `0x` + 64 hex chars. |
| `RELAY_API_KEY` | [polymarket.com/settings](https://polymarket.com/settings) → API Keys tab |
| `RELAY_API_KEY_ADDRESS` | Same page — the EOA address that owns the key |
| Docker | Must be installed and running on your computer |


## Steps (3 commands) / 三步完成

```bash
# Step 1: Clone and configure
git clone https://github.com/PWEI07/polymarket-order-signer.git
cd polymarket-order-signer
cp .env.example .env
#    ← Edit .env: fill in PRIVATE_KEY, RELAY_API_KEY, RELAY_API_KEY_ADDRESS

# Step 2: One-time setup (creates wallet + generates credentials)
docker compose run --rm setup

# Step 3: Deploy to Fly.io (one command does everything)
./scripts/deploy-fly.sh
```

Step 3 will automatically:
- Install the Fly CLI if you don't have it
- Sign you up / log you in to Fly.io
- Upload your secrets (encrypted — only you can see them)
- Deploy the signer service
- **Print a block of text — copy it and send to your trading operator**

第三步会自动安装 Fly CLI、注册登录、加密上传密钥、部署服务。最后会打印一段文本，直接复制发给交易执行方即可。

To redeploy after changes: `./scripts/deploy-fly.sh update`

## After Deploy / 部署完成后

1. **Fund your deposit wallet** with pUSD (the address is in the printed output)
2. **Send the printed block** to your trading operator — it contains everything they need
3. Done. The signer runs 24/7 on Fly.io and auto-sleeps when idle (~$2/month)

## Security / 安全说明

| Your trading operator CAN | Your trading operator CANNOT |
|---------------------------|------------------------------|
| Sign trade orders via the signer | See your private key |
| Query your open orders | Sign withdrawals or transfers |
| | Access funds directly |

Your private key stays encrypted in your Fly.io account. The signer service only signs trade orders — it rejects all other operations (withdrawals, transfers, arbitrary messages).

如果需要停止交易，直接在 Fly.io 上关掉服务即可：`fly scale count 0`

## Alternative: Local Docker / 本地运行

If you prefer to run locally instead of Fly.io (requires a server with a stable IP):

```bash
docker compose up -d signer
# Your signer URL is http://YOUR-SERVER-IP:8080
```

## Troubleshooting

| Error | Fix |
|-------|-----|
| `RELAY_API_KEY_ADDRESS must match...` | `RELAY_API_KEY_ADDRESS` must be the MetaMask address that matches your `PRIVATE_KEY` |
| `Missing required values in .env` | Fill in all 3 values in `.env` before running setup |
| `PRIVATE_KEY must be 0x + 64 hex` | Export the correct key from MetaMask (66 chars total, starts with `0x`) |
| `Relayer WALLET-CREATE failed: 401` | Your `RELAY_API_KEY` is invalid or expired — regenerate at polymarket.com/settings |
| `not enough balance` | Fund your deposit wallet address with pUSD first |
