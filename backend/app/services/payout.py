"""Payout service: "mock" (no network) or "cashfree" (Cashfree Payouts V2, sandbox by default).

Every function returns a result dict and never raises, so a payout problem can't break ending a session:
    {status, ref, provider, provider_ref, provider_status, error}
status is ours (success | pending | failed | skipped); provider_status is Cashfree's raw value.
"""
import base64
import logging
import time
import uuid

import requests

log = logging.getLogger(__name__)

CASHFREE_BASE = {
    "sandbox": "https://sandbox.cashfree.com/payout",
    "production": "https://api.cashfree.com/payout",
}
CASHFREE_API_VERSION = "2024-01-01"  # current version in the Payouts V2 docs
HTTP_TIMEOUT_S = 10

# Cashfree transfer statuses (Standard Transfer V2 / Get Transfer Status V2)
CF_SUCCESS = {"SUCCESS"}
CF_PENDING = {"RECEIVED", "QUEUED", "PENDING", "APPROVAL_PENDING", "VALIDATION_PENDING"}
CF_FAILED = {"FAILED", "REJECTED", "REVERSED", "MANUALLY_REJECTED"}


def _result(status, ref=None, provider=None, provider_ref=None, provider_status=None, error=None):
    """Build the common result dict."""
    return {"status": status, "ref": ref, "provider": provider, "provider_ref": provider_ref,
            "provider_status": provider_status, "error": error}


def map_cf_status(cf_status):
    """Cashfree status -> our payout_status."""
    if cf_status in CF_SUCCESS:
        return "success"
    if cf_status in CF_PENDING:
        return "pending"
    return "failed"


def transfer_id_for(session_id):
    """Cashfree transfer_id: unique per session, <=40 chars, alphanumeric + underscore."""
    return f"RVM_{session_id}"[:40]


def send_payout(upi_id, amount, session_id, config):
    """Pay `amount` rupees to `upi_id` for a session."""
    if amount <= 0:
        return _result("skipped")

    mode = config.get("PAYOUT_MODE", "mock")
    if mode == "mock":
        ref = f"MOCK_{uuid.uuid4().hex[:12].upper()}"
        log.info("[MOCK PAYOUT] Rs %s -> %s (session %s, ref %s)", amount, upi_id, session_id, ref)
        return _result("success", ref, provider="mock", provider_status="SUCCESS")
    if mode == "cashfree":
        return cashfree_transfer(upi_id, amount, session_id, config)

    log.error("Unsupported PAYOUT_MODE %r - payout not sent", mode)
    return _result("failed", provider=mode, provider_status="ERROR", error=f"unsupported PAYOUT_MODE {mode}")


# ---------- Cashfree ----------

def _cashfree_setup(config):
    """Return (base_url, error) after checking keys and environment."""
    if not (config.get("CASHFREE_CLIENT_ID") and config.get("CASHFREE_CLIENT_SECRET")):
        return None, "CASHFREE_CLIENT_ID / CASHFREE_CLIENT_SECRET missing in .env"
    env = config.get("CASHFREE_ENV", "sandbox")
    if env not in CASHFREE_BASE:
        return None, f"CASHFREE_ENV must be sandbox or production, got {env!r}"
    return CASHFREE_BASE[env], None


def _cashfree_headers(config):
    """Auth headers; adds the 2FA signature when a public key is configured."""
    headers = {
        "x-client-id": config["CASHFREE_CLIENT_ID"],
        "x-client-secret": config["CASHFREE_CLIENT_SECRET"],
        "x-api-version": CASHFREE_API_VERSION,
        "x-request-id": uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    key_path = config.get("CASHFREE_PUBLIC_KEY_PATH")
    if key_path:
        headers["x-cf-signature"] = cashfree_signature(config["CASHFREE_CLIENT_ID"], key_path)
    return headers


def cashfree_signature(client_id, key_path):
    """RSA-OAEP (SHA1) encrypt "clientId.unixTimestamp" with Cashfree's public key, base64 (Payouts 2FA)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    with open(key_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())
    message = f"{client_id}.{int(time.time())}".encode()
    encrypted = public_key.encrypt(
        message, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
    )
    return base64.b64encode(encrypted).decode()


def _error_text(resp):
    """Best-effort error message from a Cashfree error response."""
    try:
        body = resp.json()
        return f"HTTP {resp.status_code} {body.get('code', '')}: {body.get('message', body)}".strip()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"


def _from_transfer_body(body, transfer_id):
    """Result dict from a Cashfree transfer object (create or status response)."""
    cf_status = str(body.get("status", "")).upper() or "UNKNOWN"
    status = map_cf_status(cf_status)
    error = None
    if status == "failed":
        error = f"{body.get('status_code', cf_status)}: {body.get('status_description', '')}".strip(": ")
    return _result(status, transfer_id, provider="cashfree", provider_ref=body.get("cf_transfer_id"),
                   provider_status=cf_status, error=error)


def cashfree_transfer(upi_id, amount, session_id, config):
    """Standard Transfer V2 to a UPI ID (beneficiary details inline, no pre-registration)."""
    transfer_id = transfer_id_for(session_id)
    base, setup_error = _cashfree_setup(config)
    if setup_error:
        log.error("[CASHFREE] %s", setup_error)
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=setup_error)

    payload = {
        "transfer_id": transfer_id,
        "transfer_amount": float(amount),
        "transfer_currency": "INR",
        "transfer_mode": "upi",
        "transfer_remarks": "RVM recycling reward",
        "beneficiary_details": {
            "beneficiary_name": "RVM User",
            "beneficiary_instrument_details": {"vpa": upi_id},
        },
    }
    try:
        resp = requests.post(f"{base}/transfers", headers=_cashfree_headers(config), json=payload,
                             timeout=HTTP_TIMEOUT_S)
    except requests.Timeout:
        # The transfer may still have been created: leave it for "Refresh status", never re-send
        log.error("[CASHFREE] transfer %s timed out after %ss", transfer_id, HTTP_TIMEOUT_S)
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR",
                       error=f"timeout after {HTTP_TIMEOUT_S}s - use Refresh status")
    except (requests.RequestException, OSError, ValueError) as exc:
        log.error("[CASHFREE] transfer %s request error: %s", transfer_id, exc)
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=str(exc)[:300])

    if resp.status_code >= 500:
        # Docs: on 5XX don't initiate another transfer, check status instead
        error = _error_text(resp) + " - use Refresh status"
        log.error("[CASHFREE] transfer %s server error: %s", transfer_id, error)
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=error)
    if resp.status_code not in (200, 201):
        error = _error_text(resp)
        log.error("[CASHFREE] transfer %s rejected: %s", transfer_id, error)
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=error)

    result = _from_transfer_body(resp.json(), transfer_id)
    log.info("[CASHFREE] Rs %s -> %s transfer %s cf_id %s status %s",
             amount, upi_id, transfer_id, result["provider_ref"], result["provider_status"])
    return result


def cashfree_status(transfer_id, config):
    """Get Transfer Status V2 by our transfer_id."""
    base, setup_error = _cashfree_setup(config)
    if setup_error:
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=setup_error)
    try:
        resp = requests.get(f"{base}/transfers", headers=_cashfree_headers(config),
                            params={"transfer_id": transfer_id}, timeout=HTTP_TIMEOUT_S)
    except (requests.RequestException, OSError, ValueError) as exc:
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=str(exc)[:300])

    if resp.status_code == 404:
        # Cashfree never received this transfer (e.g. auth/IP error at send time)
        return _result("failed", transfer_id, "cashfree", provider_status="NOT_FOUND", error=_error_text(resp))
    if resp.status_code != 200:
        return _result("failed", transfer_id, "cashfree", provider_status="ERROR", error=_error_text(resp))

    result = _from_transfer_body(resp.json(), transfer_id)
    log.info("[CASHFREE] status %s -> %s", transfer_id, result["provider_status"])
    return result
