"""Payout service tests; all Cashfree HTTP calls are faked."""
import base64
import sqlite3

import pytest
import requests

from app import create_app
from app.services import payout
from app.services.payout import cashfree_status, send_payout, transfer_id_for

CF_CONFIG = {
    "PAYOUT_MODE": "cashfree",
    "CASHFREE_ENV": "sandbox",
    "CASHFREE_CLIENT_ID": "TEST_ID",
    "CASHFREE_CLIENT_SECRET": "TEST_SECRET",
    "CASHFREE_PUBLIC_KEY_PATH": "",
}
KEY = "k"
HEADERS = {"X-Machine-Key": KEY}


class FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def transfer_body(status, **extra):
    """A Cashfree transfer object as returned by the V2 APIs."""
    return {"transfer_id": "t", "cf_transfer_id": "CF123", "status": status,
            "status_code": extra.pop("status_code", status), **extra}


@pytest.fixture
def cf(monkeypatch):
    """Fake requests.post/get. Set cf.post_reply / cf.get_reply; inspect cf.calls."""
    class State:
        calls = []
        post_reply = FakeResp(200, transfer_body("SUCCESS", status_code="COMPLETED"))
        get_reply = FakeResp(200, transfer_body("SUCCESS", status_code="COMPLETED"))

    state = State()
    state.calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        state.calls.append({"method": "POST", "url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(state.post_reply, Exception):
            raise state.post_reply
        return state.post_reply

    def fake_get(url, headers=None, params=None, timeout=None):
        state.calls.append({"method": "GET", "url": url, "headers": headers, "params": params, "timeout": timeout})
        if isinstance(state.get_reply, Exception):
            raise state.get_reply
        return state.get_reply

    monkeypatch.setattr(payout.requests, "post", fake_post)
    monkeypatch.setattr(payout.requests, "get", fake_get)
    return state


# ---------- mock / generic ----------

def test_mock_mode():
    res = send_payout("a@ybl", 3, "sid", {"PAYOUT_MODE": "mock"})
    assert res["status"] == "success" and res["ref"].startswith("MOCK_")
    assert res["provider"] == "mock"


def test_zero_amount_skips_without_network(cf):
    assert send_payout("a@ybl", 0, "sid", CF_CONFIG)["status"] == "skipped"
    assert cf.calls == []


def test_unknown_mode_fails_safely():
    res = send_payout("a@ybl", 3, "sid", {"PAYOUT_MODE": "paypal"})
    assert res["status"] == "failed" and "paypal" in res["error"]


def test_transfer_id_fits_cashfree_limits():
    tid = transfer_id_for("5c6fbeccb97745e9b719122ee13280b3")
    assert len(tid) <= 40 and tid.replace("_", "").isalnum()


# ---------- Standard Transfer V2 ----------

def test_cashfree_success(cf):
    res = send_payout("success@upi", 3, "abc123", CF_CONFIG)
    assert res == {"status": "success", "ref": "RVM_abc123", "provider": "cashfree", "provider_ref": "CF123",
                   "provider_status": "SUCCESS", "error": None}

    (call,) = cf.calls  # one call: beneficiary is sent inline, no pre-registration
    assert call["url"] == "https://sandbox.cashfree.com/payout/transfers"
    assert call["timeout"] == 10
    assert call["headers"]["x-client-id"] == "TEST_ID"
    assert call["headers"]["x-api-version"] == "2024-01-01"
    assert call["headers"]["x-request-id"]
    body = call["json"]
    assert body["transfer_id"] == "RVM_abc123"
    assert body["transfer_amount"] == 3.0
    assert body["transfer_mode"] == "upi"
    assert body["beneficiary_details"]["beneficiary_name"] == "RVM User"
    assert body["beneficiary_details"]["beneficiary_instrument_details"]["vpa"] == "success@upi"


@pytest.mark.parametrize("cf_status", ["RECEIVED", "QUEUED", "PENDING", "APPROVAL_PENDING", "VALIDATION_PENDING"])
def test_cashfree_pending(cf, cf_status):
    cf.post_reply = FakeResp(200, transfer_body(cf_status))
    res = send_payout("success@upi", 2, "s1", CF_CONFIG)
    assert res["status"] == "pending"
    assert res["provider_status"] == cf_status
    assert res["error"] is None


@pytest.mark.parametrize("cf_status", ["FAILED", "REJECTED", "REVERSED", "MANUALLY_REJECTED"])
def test_cashfree_failure_status(cf, cf_status):
    cf.post_reply = FakeResp(200, transfer_body(cf_status, status_code="BENE_BANK_DECLINED",
                                                status_description="Declined by bank"))
    res = send_payout("failure@upi", 2, "s1", CF_CONFIG)
    assert res["status"] == "failed"
    assert res["provider_status"] == cf_status
    assert "BENE_BANK_DECLINED" in res["error"]


def test_cashfree_rejected_request(cf):
    cf.post_reply = FakeResp(403, {"type": "authentication_error", "code": "ip_not_whitelisted",
                                   "message": "IP not whitelisted"})
    res = send_payout("success@upi", 2, "s1", CF_CONFIG)
    assert res["status"] == "failed" and res["provider_status"] == "ERROR"
    assert "IP not whitelisted" in res["error"]
    assert res["ref"] == "RVM_s1"  # kept, so the transfer can be looked up later


def test_cashfree_5xx_is_not_resent(cf):
    cf.post_reply = FakeResp(502, {"message": "bad gateway"})
    res = send_payout("success@upi", 2, "s1", CF_CONFIG)
    assert res["status"] == "failed" and "Refresh status" in res["error"]
    assert len(cf.calls) == 1


def test_cashfree_timeout(cf):
    cf.post_reply = requests.Timeout("slow")
    res = send_payout("success@upi", 2, "s1", CF_CONFIG)
    assert res["status"] == "failed" and "timeout" in res["error"]


def test_cashfree_network_error(cf):
    cf.post_reply = requests.ConnectionError("proxy refused")
    assert send_payout("success@upi", 2, "s1", CF_CONFIG)["status"] == "failed"


def test_cashfree_missing_keys(cf):
    res = send_payout("success@upi", 2, "s1", {**CF_CONFIG, "CASHFREE_CLIENT_SECRET": ""})
    assert res["status"] == "failed" and "missing" in res["error"]
    assert cf.calls == []


def test_cashfree_signature_header(cf, tmp_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    key_file = tmp_path / "cf_public.pem"
    key_file.write_bytes(pem)

    send_payout("success@upi", 2, "s1", {**CF_CONFIG, "CASHFREE_PUBLIC_KEY_PATH": str(key_file)})
    sig = cf.calls[0]["headers"]["x-cf-signature"]
    plain = private.decrypt(base64.b64decode(sig),
                            padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    client_id, _, ts = plain.decode().partition(".")
    assert client_id == "TEST_ID" and ts.isdigit()


# ---------- Get Transfer Status V2 ----------

def test_status_success(cf):
    res = cashfree_status("RVM_s1", CF_CONFIG)
    (call,) = cf.calls
    assert call["method"] == "GET"
    assert call["url"] == "https://sandbox.cashfree.com/payout/transfers"
    assert call["params"] == {"transfer_id": "RVM_s1"}
    assert res["status"] == "success" and res["provider_ref"] == "CF123"


def test_status_not_found(cf):
    cf.get_reply = FakeResp(404, {"code": "transfer_not_found", "message": "Transfer does not exist"})
    res = cashfree_status("RVM_s1", CF_CONFIG)
    assert res["status"] == "failed" and res["provider_status"] == "NOT_FOUND"


# ---------- through the app ----------

@pytest.fixture
def app(tmp_path):
    """App in cashfree mode with a fresh DB."""
    return create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "MACHINE_KEY": KEY,
                       "MACHINE_IDS": ["RVM-01"], "ADMIN_PASSWORD": "pw", **CF_CONFIG})


def run_session(client, upi="success@upi", items=("can",)):
    """Start, deposit, end via the machine API; returns the end response JSON."""
    sid = client.post("/api/session/start", json={"machine_id": "RVM-01", "upi_id": upi}).get_json()["session_id"]
    for item in items:
        client.post("/api/deposit", json={"machine_id": "RVM-01", "session_id": sid, "type": item}, headers=HEADERS)
    return client.post("/api/machine/session/end", json={"machine_id": "RVM-01", "session_id": sid},
                       headers=HEADERS).get_json()


def admin_auth():
    """Basic auth header for the admin page."""
    return {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


def test_end_session_saves_cashfree_fields(cf, app):
    c = app.test_client()
    res = run_session(c)
    assert res["payout_status"] == "success"
    assert res["provider"] == "cashfree"
    assert res["provider_ref"] == "CF123"
    assert res["provider_status"] == "SUCCESS"
    assert "provider_error" not in res  # error text stays admin-only

    status = c.get(f"/api/session/{res['session_id']}").get_json()
    assert status["provider_status"] == "SUCCESS"


def test_api_failure_does_not_break_session_end(cf, app):
    cf.post_reply = requests.ConnectionError("down")
    res = run_session(app.test_client())
    assert res["ok"] is True and res["status"] == "ended"
    assert res["payout_status"] == "failed"


def test_admin_refresh_pending_to_success(cf, app):
    cf.post_reply = FakeResp(200, transfer_body("PENDING"))
    c = app.test_client()
    sid = run_session(c)["session_id"]

    html = c.get("/admin", headers=admin_auth()).get_data(as_text=True)
    assert "Refresh status" in html and "PENDING" in html

    cf.get_reply = FakeResp(200, transfer_body("SUCCESS"))
    res = c.post(f"/admin/refresh/{sid}", headers=admin_auth())
    assert res.status_code == 302
    assert c.get(f"/api/session/{sid}").get_json()["payout_status"] == "success"


def test_admin_refresh_needs_login(cf, app):
    assert app.test_client().post("/admin/refresh/x").status_code == 401


def test_refresh_all_and_keep_status_on_network_error(cf, app):
    cf.post_reply = FakeResp(200, transfer_body("RECEIVED"))
    c = app.test_client()
    sid = run_session(c)["session_id"]

    cf.get_reply = requests.ConnectionError("down")
    c.post("/admin/refresh", headers=admin_auth())
    s = c.get(f"/api/session/{sid}").get_json()
    assert s["payout_status"] == "pending" and s["provider_status"] == "RECEIVED"


def test_old_database_is_migrated(tmp_path):
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, upi_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', item_count INTEGER NOT NULL DEFAULT 0,
            total_amount INTEGER NOT NULL DEFAULT 0, end_reason TEXT, payout_status TEXT, payout_ref TEXT,
            created_at REAL NOT NULL, last_activity_at REAL NOT NULL, ended_at REAL);
        INSERT INTO sessions (id, machine_id, upi_id, status, created_at, last_activity_at, payout_ref)
            VALUES ('old1', 'RVM-01', 'a@ybl', 'ended', 1, 1, 'MOCK_X');
    """)
    con.commit()
    con.close()

    create_app({"TESTING": True, "DATABASE_PATH": str(db_path)})
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(sessions)")}
    assert {"provider", "provider_ref", "provider_status", "provider_error"} <= cols
    assert con.execute("SELECT payout_ref FROM sessions WHERE id = 'old1'").fetchone()[0] == "MOCK_X"
    con.close()
