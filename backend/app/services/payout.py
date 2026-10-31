"""Payout service. Only a mock for now - no real money moves."""
import logging
import uuid

log = logging.getLogger(__name__)


def send_payout(upi_id, amount, session_id, mode="mock"):
    """Send `amount` rupees to `upi_id`; returns {status, ref}."""
    if amount <= 0:
        return {"status": "skipped", "ref": None}

    if mode != "mock":
        raise ValueError(f"Unsupported PAYOUT_MODE: {mode}")

    ref = f"MOCK_{uuid.uuid4().hex[:12].upper()}"
    log.info("[MOCK PAYOUT] Rs %s -> %s (session %s, ref %s)", amount, upi_id, session_id, ref)
    return {"status": "success", "ref": ref}
