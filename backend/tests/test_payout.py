"""Payout service tests; Cashfree HTTP calls are faked."""
import base64

import pytest
import requests

from app import create_app
from app.services import payout
from app.services.payout import beneficiary_id_for, send_payout

CF_CONFIG = {
    "PAYOUT_MODE": "cashfree",
    "CASHFREE_ENV": "sandbox",
    "CASHFREE_CLIENT_ID": "TEST_ID",
    "CASHFREE_CLIENT_SECRET": "TEST_SECRET",
    "CASHFREE_PUBLIC_KEY_PATH": "",
}


class FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture
def fake_cf(monkeypatch):
    """Patch requests.post; returns the list of calls and a dict of canned responses."""
    calls = []
    replies = {
        "beneficiary": FakeResp(201, {"beneficiary_id": "x"}),
        "transfers": FakeResp(200, {"transfer_id": "t", "cf_transfer_id": "123", "status": "RECEIVED"}),
    }

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return replies[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(payout.requests, "post", fake_post)
    return calls, replies


def test_mock_mode():
    res = send_payout("a@ybl", 3, "sid", {"PAYOUT_MODE": "mock"})
    assert res["status"] == "success" and res["ref"].startswith("MOCK_")


def test_zero_amount_skips_without_network(fake_cf):
    calls, _ = fake_cf
    assert send_payout("a@ybl", 0, "sid", CF_CONFIG) == {"status": "skipped", "ref": None}
    assert calls == []


def test_unknown_mode_fails_safely():
    assert send_payout("a@ybl", 3, "sid", {"PAYOUT_MODE": "paypal"})["status"] == "failed"


def test_cashfree_success(fake_cf):
    calls, _ = fake_cf
    res = send_payout("test@ybl", 3, "abc123", CF_CONFIG)
    assert res == {"status": "success", "ref": "RVM_abc123"}

    bene, transfer = calls
    assert bene["url"] == "https://sandbox.cashfree.com/payout/beneficiary"
    assert bene["json"]["beneficiary_instrument_details"]["vpa"] == "test@ybl"
    assert bene["headers"]["x-client-id"] == "TEST_ID"
    assert bene["headers"]["x-api-version"] == "2024-01-01"
    assert transfer["url"] == "https://sandbox.cashfree.com/payout/transfers"
    assert transfer["json"]["transfer_amount"] == 3.0
    assert transfer["json"]["transfer_mode"] == "upi"
    assert transfer["json"]["beneficiary_details"]["beneficiary_id"] == beneficiary_id_for("test@ybl")


def test_cashfree_reuses_existing_beneficiary(fake_cf):
    _, replies = fake_cf
    replies["beneficiary"] = FakeResp(409, {"code": "beneficiary_id_already_exists"})
    assert send_payout("test@ybl", 2, "s1", CF_CONFIG)["status"] == "success"


def test_cashfree_transfer_rejected(fake_cf):
    _, replies = fake_cf
    replies["transfers"] = FakeResp(400, {"code": "insufficient_balance", "message": "Low balance"})
    assert send_payout("test@ybl", 2, "s1", CF_CONFIG) == {"status": "failed", "ref": None}


def test_cashfree_failed_status(fake_cf):
    _, replies = fake_cf
    replies["transfers"] = FakeResp(200, {"status": "REJECTED"})
    assert send_payout("test@ybl", 2, "s1", CF_CONFIG)["status"] == "failed"


def test_cashfree_beneficiary_error(fake_cf):
    calls, replies = fake_cf
    replies["beneficiary"] = FakeResp(401, {"code": "authentication_failed"})
    assert send_payout("test@ybl", 2, "s1", CF_CONFIG)["status"] == "failed"
    assert len(calls) == 1  # no transfer attempted


def test_cashfree_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("proxy refused")
    monkeypatch.setattr(payout.requests, "post", boom)
    assert send_payout("test@ybl", 2, "s1", CF_CONFIG)["status"] == "failed"


def test_cashfree_missing_keys(fake_cf):
    calls, _ = fake_cf
    cfg = {**CF_CONFIG, "CASHFREE_CLIENT_SECRET": ""}
    assert send_payout("test@ybl", 2, "s1", cfg)["status"] == "failed"
    assert calls == []


def test_cashfree_signature_header(fake_cf, tmp_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    key_file = tmp_path / "cf_public.pem"
    key_file.write_bytes(pem)

    calls, _ = fake_cf
    send_payout("test@ybl", 2, "s1", {**CF_CONFIG, "CASHFREE_PUBLIC_KEY_PATH": str(key_file)})
    sig = calls[0]["headers"]["x-cf-signature"]
    plain = private.decrypt(base64.b64decode(sig),
                            padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    assert plain.decode().startswith("TEST_ID.")


def test_end_session_uses_cashfree(fake_cf, tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "MACHINE_KEY": "k",
                      "MACHINE_IDS": ["RVM-01"], **CF_CONFIG})
    c = app.test_client()
    sid = c.post("/api/session/start", json={"machine_id": "RVM-01", "upi_id": "test@ybl"}).get_json()["session_id"]
    h = {"X-Machine-Key": "k"}
    c.post("/api/deposit", json={"machine_id": "RVM-01", "session_id": sid, "type": "can"}, headers=h)
    res = c.post("/api/machine/session/end", json={"machine_id": "RVM-01", "session_id": sid}, headers=h).get_json()
    assert res["payout_status"] == "success"
    assert res["payout_ref"] == f"RVM_{sid}"
