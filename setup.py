#!/usr/bin/env python3
"""Turnkey setup for Polymarket deposit wallet trading.

Runs 4 idempotent steps:
  1. Deploy deposit wallet via Relayer
  2. Approve trading contracts from the deposit wallet
  3. Generate CLOB API credentials
  4. Verify everything works

Usage:
  python setup.py              # reads .env in current dir
  python setup.py --env /path  # reads specified env file
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

ENV_PATH = Path(__file__).resolve().parent / ".env"

# ── Polymarket contract addresses (Polygon mainnet) ─────────────────────────

DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
CTF_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"

RELAYER_URL = "https://relayer-v2.polymarket.com"
RPC_URL = "https://polygon-bor-rpc.publicnode.com"

MAX_UINT256 = 2**256 - 1
w3 = Web3(Web3.HTTPProvider(RPC_URL))


# ── Helpers ──────────────────────────────────────────────────────────────────

def die(msg: str):
    print(f"\n  ERROR: {msg}\n", file=sys.stderr)
    sys.exit(1)


def upsert_env(env_path: Path, key: str, value: str):
    """Write or update a key=value in the env file."""
    text = env_path.read_text() if env_path.exists() else ""
    pattern = re.compile(rf"^#?\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    env_path.write_text(text)


def relayer_headers(env: dict) -> dict:
    return {
        "Content-Type": "application/json",
        "RELAYER_API_KEY": env["RELAY_API_KEY"],
        "RELAYER_API_KEY_ADDRESS": env["RELAY_API_KEY_ADDRESS"],
    }


def wait_for_tx(tx_hash: str, timeout: int = 180) -> dict:
    """Poll Polygon RPC until tx is mined. Returns receipt."""
    for _ in range(timeout // 3):
        time.sleep(3)
        r = requests.post(RPC_URL, json={
            "jsonrpc": "2.0", "method": "eth_getTransactionReceipt",
            "params": [tx_hash], "id": 1,
        }, timeout=15)
        receipt = r.json().get("result")
        if receipt:
            return receipt
    die(f"Transaction {tx_hash} not confirmed within {timeout}s")
    return {}  # unreachable


def has_bytecode(address: str) -> bool:
    r = requests.post(RPC_URL, json={
        "jsonrpc": "2.0", "method": "eth_getCode",
        "params": [address, "latest"], "id": 1,
    }, timeout=10)
    code = r.json().get("result", "0x")
    return code != "0x" and len(code) > 2


def normalize_env(raw: dict) -> dict:
    """Map alternate key names (e.g. from Polymarket docs) to canonical RELAY_* names."""
    env = dict(raw)
    if not (env.get("RELAY_API_KEY") or "").strip() and (env.get("relay_api_key") or "").strip():
        env["RELAY_API_KEY"] = str(env["relay_api_key"]).strip()
    if not (env.get("RELAY_API_KEY_ADDRESS") or "").strip() and (
        env.get("relay_api_key_address") or ""
    ).strip():
        env["RELAY_API_KEY_ADDRESS"] = str(env["relay_api_key_address"]).strip()
    return env


def clob_collateral_allowance(deposit_wallet: str) -> int:
    """pUSD allowance from deposit wallet to the main CTF exchange (proxy for full approval set)."""
    erc20 = w3.eth.contract(
        address=w3.to_checksum_address(PUSD),
        abi=[{
            "name": "allowance",
            "type": "function",
            "inputs": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
            ],
            "outputs": [{"type": "uint256"}],
            "stateMutability": "view",
        }],
    )
    return erc20.functions.allowance(
        w3.to_checksum_address(deposit_wallet),
        w3.to_checksum_address(CTF_EXCHANGE),
    ).call()


def approvals_already_sufficient(deposit_wallet: str) -> bool:
    """Skip WALLET batch if pUSD already approved for CTF exchange (idempotent re-runs)."""
    try:
        if clob_collateral_allowance(deposit_wallet) > 2**200:
            return True
    except Exception as exc:
        print(f"  (allowance check failed, will try approvals batch: {exc})", flush=True)
    return False


# ── Step 1: Deploy deposit wallet ───────────────────────────────────────────

def step_deploy_wallet(env: dict, env_path: Path) -> str:
    print("\nStep 1/4: Deploying deposit wallet...")

    eoa = env["RELAY_API_KEY_ADDRESS"]

    # Check if already deployed
    existing = env.get("DEPOSIT_WALLET", "").strip()
    if existing and has_bytecode(existing):
        print(f"  -> Already deployed at {existing}")
        return existing

    r = requests.post(f"{RELAYER_URL}/submit", headers=relayer_headers(env), json={
        "type": "WALLET-CREATE",
        "from": eoa,
        "to": DEPOSIT_WALLET_FACTORY,
    }, timeout=30)

    if r.status_code != 200:
        die(f"Relayer WALLET-CREATE failed: {r.status_code} {r.text}")

    tx_hash = r.json()["transactionHash"]
    print(f"  -> TX submitted: {tx_hash}")

    receipt = wait_for_tx(tx_hash)
    if int(receipt["status"], 16) != 1:
        die(f"WALLET-CREATE transaction reverted: {tx_hash}")

    factory_lower = DEPOSIT_WALLET_FACTORY.lower()
    deposit_wallet = None
    for log in receipt.get("logs", []):
        if log["address"].lower() == factory_lower and len(log["topics"]) >= 2:
            deposit_wallet = w3.to_checksum_address("0x" + log["topics"][1][-40:])
            break

    if not deposit_wallet:
        die("Could not find WalletDeployed event in transaction logs")

    if not has_bytecode(deposit_wallet):
        die(f"Deposit wallet {deposit_wallet} has no bytecode after deployment")

    print(f"  -> Deposit wallet: {deposit_wallet}")
    print(f"  -> Verified on-chain (bytecode present)")

    upsert_env(env_path, "DEPOSIT_WALLET", deposit_wallet)
    print(f"  -> Updated .env: DEPOSIT_WALLET={deposit_wallet}")

    return deposit_wallet


# ── Step 2: Approve trading contracts ────────────────────────────────────────

def step_approve_contracts(env: dict, deposit_wallet: str):
    print("\nStep 2/4: Approving trading contracts...")

    if approvals_already_sufficient(deposit_wallet):
        print("  -> Approvals already set on-chain; skipping WALLET batch")
        return

    private_key = env["PRIVATE_KEY"]
    eoa = env["RELAY_API_KEY_ADDRESS"]

    erc20 = w3.eth.contract(
        address=w3.to_checksum_address(PUSD),
        abi=[{"name": "approve", "type": "function",
              "inputs": [{"name": "spender", "type": "address"},
                         {"name": "amount", "type": "uint256"}],
              "outputs": [{"type": "bool"}]}],
    )
    erc1155 = w3.eth.contract(
        address=w3.to_checksum_address(CTF),
        abi=[{"name": "setApprovalForAll", "type": "function",
              "inputs": [{"name": "operator", "type": "address"},
                         {"name": "approved", "type": "bool"}],
              "outputs": []}],
    )

    calls = [
        {"target": PUSD, "value": "0",
         "data": erc20.encode_abi("approve", [w3.to_checksum_address(CTF_EXCHANGE), MAX_UINT256])},
        {"target": PUSD, "value": "0",
         "data": erc20.encode_abi("approve", [w3.to_checksum_address(NEG_RISK_EXCHANGE), MAX_UINT256])},
        {"target": PUSD, "value": "0",
         "data": erc20.encode_abi("approve", [w3.to_checksum_address(CTF_ADAPTER), MAX_UINT256])},
        {"target": PUSD, "value": "0",
         "data": erc20.encode_abi("approve", [w3.to_checksum_address(NEG_RISK_ADAPTER), MAX_UINT256])},
        {"target": CTF, "value": "0",
         "data": erc1155.encode_abi("setApprovalForAll", [w3.to_checksum_address(CTF_EXCHANGE), True])},
        {"target": CTF, "value": "0",
         "data": erc1155.encode_abi("setApprovalForAll", [w3.to_checksum_address(NEG_RISK_EXCHANGE), True])},
    ]

    # Get nonce
    r = requests.get(
        f"{RELAYER_URL}/nonce?address={eoa}&type=WALLET",
        headers=relayer_headers(env), timeout=10,
    )
    if r.status_code != 200:
        die(f"Failed to get nonce: {r.status_code} {r.text}")
    nonce = r.json()["nonce"]
    deadline = str(int(time.time()) + 600)

    dw_cs = w3.to_checksum_address(deposit_wallet)

    signable = encode_typed_data(
        domain_data={
            "name": "DepositWallet", "version": "1",
            "chainId": 137, "verifyingContract": dw_cs,
        },
        message_types={
            "Call": [
                {"name": "target", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
            ],
            "Batch": [
                {"name": "wallet", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "calls", "type": "Call[]"},
            ],
        },
        message_data={
            "wallet": dw_cs,
            "nonce": int(nonce),
            "deadline": int(deadline),
            "calls": [
                {
                    "target": w3.to_checksum_address(c["target"]),
                    "value": 0,
                    "data": bytes.fromhex(c["data"][2:]),
                }
                for c in calls
            ],
        },
    )
    signed = Account.sign_message(signable, private_key=private_key)
    signature = "0x" + signed.signature.hex()

    r = requests.post(f"{RELAYER_URL}/submit", headers=relayer_headers(env), json={
        "type": "WALLET",
        "from": eoa,
        "to": DEPOSIT_WALLET_FACTORY,
        "nonce": nonce,
        "signature": signature,
        "depositWalletParams": {
            "depositWallet": deposit_wallet,
            "deadline": deadline,
            "calls": calls,
        },
    }, timeout=30)

    if r.status_code != 200:
        die(f"Relayer WALLET batch failed: {r.status_code} {r.text}")

    tx_hash = r.json()["transactionHash"]
    print(f"  -> 6 approvals submitted: {tx_hash}")

    receipt = wait_for_tx(tx_hash)
    status = int(receipt["status"], 16)
    if status != 1:
        die(f"Approval transaction reverted: {tx_hash}")

    print(f"  -> TX confirmed, status=1, logs={len(receipt.get('logs', []))}")


# ── Step 3: Generate CLOB credentials ───────────────────────────────────────

def step_generate_clob_creds(env: dict, deposit_wallet: str, env_path: Path):
    print("\nStep 3/4: Generating CLOB API credentials...")

    existing_key = env.get("CLOB_API_KEY", "").strip()
    if existing_key:
        print(f"  -> CLOB credentials already present (key={existing_key[:8]}...)")
        print("  -> To regenerate, remove CLOB_API_KEY from .env and re-run setup")
        return

    from py_clob_client_v2 import ClobClient, SignatureTypeV2

    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=env["PRIVATE_KEY"],
        signature_type=SignatureTypeV2.POLY_1271,
        funder=deposit_wallet,
    )

    creds = client.create_or_derive_api_key()

    api_key = creds.api_key if hasattr(creds, "api_key") else creds.get("apiKey", "")
    api_secret = creds.api_secret if hasattr(creds, "api_secret") else creds.get("secret", "")
    api_passphrase = creds.api_passphrase if hasattr(creds, "api_passphrase") else creds.get("passphrase", "")

    upsert_env(env_path, "CLOB_API_KEY", api_key)
    upsert_env(env_path, "CLOB_API_SECRET", api_secret)
    upsert_env(env_path, "CLOB_API_PASSPHRASE", api_passphrase)

    print(f"  -> API key: {api_key[:8]}...")
    print(f"  -> Updated .env: CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE")


# ── Step 4: Verify ──────────────────────────────────────────────────────────

def step_verify(env_path: Path):
    print("\nStep 4/4: Verifying setup...")

    env = normalize_env(dotenv_values(env_path))
    dw = env.get("DEPOSIT_WALLET", "").strip()
    clob_key = env.get("CLOB_API_KEY", "").strip()
    eoa = env.get("RELAY_API_KEY_ADDRESS", "").strip()

    ok = True
    if dw and has_bytecode(dw):
        print(f"  -> Deposit wallet deployed: YES ({dw})")
    else:
        print(f"  -> Deposit wallet deployed: NO")
        ok = False

    if clob_key:
        print(f"  -> CLOB credentials present: YES")
    else:
        print(f"  -> CLOB credentials present: NO")
        ok = False

    if not ok:
        die("Verification failed. Check errors above and re-run setup.")

    print(f"""
Setup complete!

Next steps:
  1. Fund your deposit wallet with pUSD:
     {dw}
  2. Deploy the signer (pick one):
     a) Cloud (recommended): see README.md "Deploy to Cloud" section
        - Run: fly launch --no-deploy && fly secrets set ... && fly deploy
     b) Local Docker: docker compose up -d signer
  3. Share with your trading operator:
     - CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE
     - DEPOSIT_WALLET
     - EOA/signer address: {eoa}
     - Signer URL (e.g. https://your-app.fly.dev or http://your-server:8080)
     - ORDER_SIGNER_AUTH_TOKEN
  4. Keep private:
     - PRIVATE_KEY (stays in this server / your Fly.io account only)
""")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket deposit wallet setup")
    parser.add_argument("--env", default=str(ENV_PATH), help="Path to .env file")
    args = parser.parse_args()

    env_path = Path(args.env).resolve()
    if not env_path.exists():
        die(f".env file not found: {env_path}\n"
            f"  Copy .env.example to .env and fill in your credentials first.")

    env = normalize_env(dotenv_values(env_path))

    required = ["PRIVATE_KEY", "RELAY_API_KEY", "RELAY_API_KEY_ADDRESS"]
    missing = [k for k in required if not env.get(k, "").strip()]
    if missing:
        die(f"Missing required values in .env: {', '.join(missing)}\n"
            f"  See .env.example for what each field means.")

    pk = env["PRIVATE_KEY"].strip()
    if not re.match(r"^0x[0-9a-fA-F]{64}$", pk):
        die("PRIVATE_KEY must be 0x + 64 hex characters")

    eoa = Account.from_key(pk).address
    relay_addr = env["RELAY_API_KEY_ADDRESS"].strip()
    if relay_addr.lower() != eoa.lower():
        die(
            "RELAY_API_KEY_ADDRESS must match the address derived from PRIVATE_KEY.\n"
            f"  From private key: {eoa}\n"
            f"  In .env:          {relay_addr}"
        )

    print(f"Polymarket Deposit Wallet Setup")
    print(f"  EOA: {eoa}")
    print(f"  Relay key: {env['RELAY_API_KEY'][:12]}...")

    deposit_wallet = step_deploy_wallet(env, env_path)
    step_approve_contracts(env, deposit_wallet)

    env = normalize_env(dotenv_values(env_path))
    step_generate_clob_creds(env, deposit_wallet, env_path)
    step_verify(env_path)


if __name__ == "__main__":
    main()
