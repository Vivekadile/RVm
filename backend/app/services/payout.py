"""Payout service: "mock" (no network) or "cashfree" (Cashfree Payouts V2, sandbox by default)."""
import base64
import hashlib
import logging
import time
import uuid

import requests

log = logging.getLogger(__name__)

CASHFREE_BASE = {
    "sandbox": "https://sandbox.cashfree.com",
    "production": "https://api.cashfree.com",
}
CASHFREE_API_VERSION = "2024-01-01"
HTTP_TIMEOUT_S = 15
# Transfer accepted by Cashfree (final SUCCESS may arrive a bit later)
ACCEPTED_STATUSES = {"SUCCESS", "RECEIVED", "PENDING", "QUEUED", "APPROVAL_PENDING"}


def send_payout(upi_id, amount, session_id, config):
    """Pay `amount` rupees to `upi_id`; returns {status: success|failed|skipped, ref}. Never raises."""
    if amount <= 0:
        return {"status": "skipped", "ref": None}

    mode = config.get("PAYOUT_MODE", "mock")
    if mode == "mock":
        ref = f"MOCK_{uuid.uuid4().hex[:12].upper()}"
        log.info("[MOCK PAYOUT] Rs %s -> %s (session %s, ref %s)", amount, upi_id, session_id, ref)
        return {"status": "success", "ref": ref}
    if mode == "cashfree":
        return _cashfree_payout(upi_id, amount, session_id, config)

    log.error("Unsupported PAYOUT_MODE %r - payout not sent", mode)
    return {"status": "failed", "ref": None}


# ---------- Cashfree ----------

def _cashfree_headers(config):
    """Auth headers; adds the 2FA signature when a public key is configured."""
    headers = {
        "x-client-id": config["CASHFREE_CLIENT_ID"],
        "x-client-secret": config["CASHFREE_CLIENT_SECRET"],
        "x-api-version": CASHFREE_API_VERSION,
        "Content-Type": "application/json",
    }
    key_path = config.get("CASHFREE_PUBLIC_KEY_PATH")
    if key_path:
        headers["x-cf-signature"] = _cashfree_signature(config["CASHFREE_CLIENT_ID"], key_path)
    return headers


def _cashfree_signature(client_id, key_path):
    """RSA-OAEP encrypt "clientId.timestamp" with Cashfree's public key (Payouts 2FA)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    with open(key_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())
    message = f"{client_id}.{int(time.time())}".encode()
    encrypted = public_key.encrypt(
        message, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
    )
    return base64.b64encode(encrypted).decode()


def beneficiary_id_for(upi_id):
    """Stable beneficiary id per UPI, so repeat users reuse the same beneficiary."""
    return "rvm_" + hashlib.sha1(upi_id.lower().encode()).hexdigest()[:24]


def _error_text(resp):
    """Best-effort error message from a Cashfree response."""
    try:
        body = resp.json()
        return f"{resp.status_code} {body.get('code', '')} {body.get('message', body)}"
    except ValueError:
        return f"{resp.status_code} {resp.text[:200]}"


def _cashfree_payout(upi_id, amount, session_id, config):
    """Create (or reuse) a UPI beneficiary, then request a transfer."""
    if not (config.get("CASHFREE_CLIENT_ID") and config.get("CASHFREE_CLIENT_SECRET")):
        log.error("[CASHFREE] CASHFREE_CLIENT_ID / CASHFREE_CLIENT_SECRET missing in .env")
        return {"status": "failed", "ref": None}

    env = config.get("CASHFREE_ENV", "sandbox")
    base = CASHFREE_BASE.get(env)
    if base is None:
        log.error("[CASHFREE] CASHFREE_ENV must be 'sandbox' or 'production', got %r", env)
        return {"status": "failed", "ref": None}

    bene_id = beneficiary_id_for(upi_id)
    # Same transfer id for the same session: Cashfree rejects duplicates, so a retry can't pay twice
    transfer_id = f"RVM_{session_id}"

    try:
        headers = _cashfree_headers(config)
        resp = requests.post(f"{base}/payout/beneficiary", headers=headers, timeout=HTTP_TIMEOUT_S, json={
            "beneficiary_id": bene_id,
            "beneficiary_name": "RVM User",
            "beneficiary_instrument_details": {"vpa": upi_id},
            "beneficiary_contact_details": {
                "beneficiary_email": "rvm.user@example.com",
                "beneficiary_phone": "9999999999",
            },
        })
        # 409 = beneficiary already exists from an earlier session: reuse it
        if resp.status_code not in (200, 201, 409):
            log.error("[CASHFREE] beneficiary %s failed: %s", bene_id, _error_text(resp))
            return {"status": "failed", "ref": None}

        resp = requests.post(f"{base}/payout/transfers", headers=headers, timeout=HTTP_TIMEOUT_S, json={
            "transfer_id": transfer_id,
            "transfer_amount": float(amount),
            "transfer_currency": "INR",
            "transfer_mode": "upi",
            "beneficiary_details": {"beneficiary_id": bene_id},
            "transfer_remarks": "RVM recycling reward",
        })
    except (requests.RequestException, OSError, ValueError) as exc:
        log.error("[CASHFREE] request error: %s", exc)
        return {"status": "failed", "ref": None}

    if resp.status_code not in (200, 201):
        log.error("[CASHFREE] transfer %s failed: %s", transfer_id, _error_text(resp))
        return {"status": "failed", "ref": None}

    body = resp.json()
    status = str(body.get("status", "")).upper()
    log.info("[CASHFREE %s] Rs %s -> %s transfer %s cf_id %s status %s",
             env.upper(), amount, upi_id, transfer_id, body.get("cf_transfer_id"), status)
    if status not in ACCEPTED_STATUSES:
        return {"status": "failed", "ref": transfer_id}
    return {"status": "success", "ref": transfer_id}
