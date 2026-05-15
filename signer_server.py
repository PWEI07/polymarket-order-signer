#!/usr/bin/env python3
"""Order-only Polymarket signer.

This service signs CLOB orders only. It intentionally does not expose generic
message signing, relayer WALLET batch signing, approvals, transfers, or submits.
"""

import os
from decimal import Decimal
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from eth_account import Account
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, validator
from typing_extensions import Literal

try:
    from py_clob_client_v2 import (
        ClobClient,
        OrderArgs,
        PartialCreateOrderOptions,
    )
except ImportError:  # Compatibility with older package layout.
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import OrderArgs

    PartialCreateOrderOptions = None


_dir = Path(__file__).resolve().parent
for _candidate in [_dir / ".env", Path("/app/.env")]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break

HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
DEPOSIT_WALLET = os.getenv("DEPOSIT_WALLET", os.getenv("deposit_wallet", "")).strip()
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", os.getenv("POLY_SIGNATURE_TYPE", "3")))
AUTH_TOKEN = os.getenv("ORDER_SIGNER_AUTH_TOKEN", "").strip()
ALLOW_UNAUTH_LOCAL = os.getenv("ALLOW_UNAUTH_LOCAL", "false").lower() == "true"

MAX_ORDER_NOTIONAL_USD = Decimal(os.getenv("MAX_ORDER_NOTIONAL_USD", "10"))
MAX_ORDER_SIZE = Decimal(os.getenv("MAX_ORDER_SIZE", "100"))
MIN_PRICE = Decimal(os.getenv("MIN_PRICE", "0.01"))
MAX_PRICE = Decimal(os.getenv("MAX_PRICE", "0.99"))
ORDER_SIGNER_ALLOW_ANY_TOKEN = os.getenv("ORDER_SIGNER_ALLOW_ANY_TOKEN", "true").lower() == "true"
ALLOWED_TOKEN_IDS = {
    token.strip()
    for token in os.getenv("ALLOWED_TOKEN_IDS", "").split(",")
    if token.strip()
}


class SignOrderRequest(BaseModel):
    token_id: str = Field(..., min_length=20)
    side: Literal["BUY", "SELL", 0, 1]
    price: Decimal
    size: Decimal
    neg_risk: bool = False
    tick_size: Literal["0.1", "0.01", "0.001", "0.0001"] = "0.01"

    @validator("price")
    def validate_price(cls, value):
        if value < MIN_PRICE or value > MAX_PRICE:
            raise ValueError(f"price must be between {MIN_PRICE} and {MAX_PRICE}")
        return value

    @validator("size")
    def validate_size(cls, value):
        if value <= 0 or value > MAX_ORDER_SIZE:
            raise ValueError(f"size must be > 0 and <= {MAX_ORDER_SIZE}")
        return value

    @property
    def side_string(self) -> str:
        if self.side in (0, "BUY"):
            return "BUY"
        return "SELL"


app = FastAPI(title="Polymarket Order-Only Signer", version="0.1.0")
_client = None  # type: Optional[ClobClient]
_signer_address = None  # type: Optional[str]


def _require_auth(authorization=Header(default=None)):
    if ALLOW_UNAUTH_LOCAL and not AUTH_TOKEN:
        return
    if not AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="ORDER_SIGNER_AUTH_TOKEN is not configured")
    if authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _serialize(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return jsonable_encoder(value)


def _get_client():
    global _client, _signer_address

    if _client is not None:
        return _client
    if not PRIVATE_KEY:
        raise HTTPException(status_code=500, detail="PRIVATE_KEY is not configured")
    if SIGNATURE_TYPE != 0 and not DEPOSIT_WALLET:
        raise HTTPException(status_code=500, detail="DEPOSIT_WALLET is required for non-EOA signing")

    acct = Account.from_key(PRIVATE_KEY)
    _signer_address = acct.address
    _client = ClobClient(
        host=HOST,
        chain_id=CHAIN_ID,
        key=PRIVATE_KEY,
        signature_type=SIGNATURE_TYPE,
        funder=DEPOSIT_WALLET if SIGNATURE_TYPE != 0 else acct.address,
    )
    return _client


@app.get("/health")
def health():
    signer = None
    if PRIVATE_KEY:
        try:
            signer = Account.from_key(PRIVATE_KEY).address
        except Exception:
            signer = "invalid-private-key"

    return {
        "ok": bool(PRIVATE_KEY),
        "signer_address": signer,
        "funder": DEPOSIT_WALLET or None,
        "signature_type": SIGNATURE_TYPE,
        "allowed_tokens": len(ALLOWED_TOKEN_IDS),  # type: ignore[arg-type]
        "allow_any_token": ORDER_SIGNER_ALLOW_ANY_TOKEN,
        "max_order_notional_usd": str(MAX_ORDER_NOTIONAL_USD),
        "max_order_size": str(MAX_ORDER_SIZE),
    }


@app.post("/sign-order", dependencies=[Depends(_require_auth)])
def sign_order(req: SignOrderRequest):
    if not ORDER_SIGNER_ALLOW_ANY_TOKEN and req.token_id not in ALLOWED_TOKEN_IDS:
        raise HTTPException(status_code=403, detail="token_id is not allowlisted")

    notional = req.price * req.size
    if notional > MAX_ORDER_NOTIONAL_USD:
        raise HTTPException(
            status_code=403,
            detail=f"order notional {notional} exceeds max {MAX_ORDER_NOTIONAL_USD}",
        )

    client = _get_client()
    order_args = OrderArgs(
        token_id=req.token_id,
        price=float(req.price),
        size=float(req.size),
        side=req.side_string,
    )

    try:
        if PartialCreateOrderOptions is not None:
            signed = client.create_order(
                order_args,
                options=PartialCreateOrderOptions(
                    tick_size=req.tick_size,
                    neg_risk=req.neg_risk,
                ),
            )
        else:
            signed = client.create_order(order_args)
    except TypeError:
        signed = client.create_order(order_args)

    return {
        "success": True,
        "signer_address": _signer_address,
        "funder": DEPOSIT_WALLET,
        "signature_type": SIGNATURE_TYPE,
        "signed_order": _serialize(signed),
    }


@app.post("/sign-wallet-batch", dependencies=[Depends(_require_auth)])
def reject_wallet_batch():
    raise HTTPException(status_code=403, detail="wallet batch signing is disabled")


@app.post("/sign-message", dependencies=[Depends(_require_auth)])
def reject_message_signing():
    raise HTTPException(status_code=403, detail="generic message signing is disabled")


@app.post("/submit-transaction", dependencies=[Depends(_require_auth)])
def reject_relayer_submit():
    raise HTTPException(status_code=403, detail="relayer submit is disabled")
