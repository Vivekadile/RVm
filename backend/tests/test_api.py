"""API tests for the RVM backend."""
import base64

import pytest

from app import create_app

MACHINE_ID = "RVM-01"
KEY = "test-machine-key"
HEADERS = {"X-Machine-Key": KEY}


@pytest.fixture
def client(tmp_path):
    """Test client backed by a fresh SQLite file."""
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "MACHINE_KEY": KEY,
        "ADMIN_PASSWORD": "pw",
        "MACHINE_IDS": [MACHINE_ID],
        "PRICE_BOTTLE": 1,
        "PRICE_CAN": 2,
        "SESSION_TIMEOUT_S": 300,
        "MAX_ITEMS_PER_SESSION": 5,
        "PAYOUT_MODE": "mock",
    })
    return app.test_client()


def start(client, upi_id="vivek@okicici"):
    """Start a session and return the JSON response."""
    res = client.post("/api/session/start", json={"machine_id": MACHINE_ID, "upi_id": upi_id})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def deposit(client, session_id, item_type, **extra):
    """Post one deposit and return the response."""
    body = {"machine_id": MACHINE_ID, "session_id": session_id, "type": item_type, **extra}
    return client.post("/api/deposit", json=body, headers=HEADERS)


def end(client, session_id, reason="done_button"):
    """End a session and return the response."""
    body = {"machine_id": MACHINE_ID, "session_id": session_id, "reason": reason}
    return client.post("/api/machine/session/end", json=body, headers=HEADERS)


# ---- health / page ----

def test_health(client):
    assert client.get("/").get_json()["ok"] is True


def test_machine_page(client):
    res = client.get(f"/m/{MACHINE_ID}")
    assert res.status_code == 200
    assert MACHINE_ID.encode() in res.data


def test_machine_page_unknown(client):
    assert client.get("/m/NOPE").status_code == 404


# ---- auth ----

def test_machine_endpoints_need_key(client):
    assert client.get(f"/api/machine/{MACHINE_ID}/session").status_code == 401
    bad = {"X-Machine-Key": "wrong"}
    assert client.get(f"/api/machine/{MACHINE_ID}/session", headers=bad).status_code == 401
    assert client.post("/api/deposit", json={}).status_code == 401
    assert client.post("/api/machine/session/end", json={}).status_code == 401


def test_empty_machine_key_rejects_everything(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "MACHINE_KEY": ""})
    res = app.test_client().get(f"/api/machine/{MACHINE_ID}/session", headers={"X-Machine-Key": ""})
    assert res.status_code == 401


# ---- session start ----

@pytest.mark.parametrize("upi_id", [
    "not-a-upi", "vivek@gmail.com", "a@b.c", "x@ybl", "@ybl", "name@", "na me@ybl", "name@y-bl",
])
def test_start_rejects_bad_upi(client, upi_id):
    res = client.post("/api/session/start", json={"machine_id": MACHINE_ID, "upi_id": upi_id})
    assert res.status_code == 400


@pytest.mark.parametrize("upi_id", ["test@ybl", "9876543210@paytm", "first.last@okicici", "ab_c-1@upi"])
def test_start_accepts_real_upi(client, upi_id):
    start(client, upi_id)


def test_start_rejects_unknown_machine(client):
    res = client.post("/api/session/start", json={"machine_id": "X", "upi_id": "a@okicici"})
    assert res.status_code == 404


def test_only_one_active_session_per_machine(client):
    start(client)
    res = client.post("/api/session/start", json={"machine_id": MACHINE_ID, "upi_id": "bob@ybl"})
    assert res.status_code == 409
    assert res.get_json()["error"].startswith("Machine in use")


# ---- poll ----

def test_poll_idle_then_active(client):
    res = client.get(f"/api/machine/{MACHINE_ID}/session", headers=HEADERS).get_json()
    assert res["active"] is False

    s = start(client)
    res = client.get(f"/api/machine/{MACHINE_ID}/session", headers=HEADERS).get_json()
    assert res["active"] is True
    assert res["session_id"] == s["session_id"]
    assert res["upi_masked"] == "vi****@okicici"


def test_poll_unknown_machine(client):
    assert client.get("/api/machine/NOPE/session", headers=HEADERS).status_code == 404


# ---- deposit ----

def test_deposit_prices_and_totals(client):
    sid = start(client)["session_id"]
    assert deposit(client, sid, "bottle").get_json()["total_amount"] == 1
    res = deposit(client, sid, "can", weight_g=15).get_json()
    assert res["amount"] == 2
    assert res["item_count"] == 2
    assert res["total_amount"] == 3
    assert (res["bottle_count"], res["can_count"]) == (1, 1)


def test_deposit_limit(client):
    sid = start(client)["session_id"]
    for _ in range(5):
        assert deposit(client, sid, "bottle").status_code == 201
    res = deposit(client, sid, "can")
    assert res.status_code == 429
    assert res.get_json()["error"] == "limit reached"


def test_deposit_validation(client):
    sid = start(client)["session_id"]
    assert deposit(client, sid, "glass").status_code == 400
    assert deposit(client, sid, "can", weight_g="heavy").status_code == 400
    assert deposit(client, "missing", "can").status_code == 404


def test_deposit_wrong_machine(client):
    sid = start(client)["session_id"]
    body = {"machine_id": "RVM-99", "session_id": sid, "type": "can"}
    assert client.post("/api/deposit", json=body, headers=HEADERS).status_code == 404


def test_deposit_after_end_rejected(client):
    sid = start(client)["session_id"]
    end(client, sid)
    assert deposit(client, sid, "bottle").status_code == 409


# ---- end + payout ----

def test_end_pays_server_side_total(client):
    sid = start(client)["session_id"]
    deposit(client, sid, "bottle")
    deposit(client, sid, "can")
    deposit(client, sid, "can")

    res = end(client, sid).get_json()
    assert res["status"] == "ended"
    assert res["total_amount"] == 5
    assert res["payout_status"] == "success"
    assert res["payout_ref"].startswith("MOCK_")
    assert res["already_ended"] is False

    # Phone sees the same result, with per-type counts
    status = client.get(f"/api/session/{sid}").get_json()
    assert status["payout_ref"] == res["payout_ref"]
    assert (status["bottle_count"], status["can_count"]) == (1, 2)


def test_end_is_idempotent(client):
    sid = start(client)["session_id"]
    deposit(client, sid, "can")
    first = end(client, sid).get_json()
    second = end(client, sid).get_json()
    assert second["already_ended"] is True
    assert second["payout_ref"] == first["payout_ref"]


def test_end_with_no_items_skips_payout(client):
    sid = start(client)["session_id"]
    res = end(client, sid).get_json()
    assert res["payout_status"] == "skipped"
    assert res["payout_ref"] is None


def test_end_bad_reason(client):
    sid = start(client)["session_id"]
    assert end(client, sid, reason="bored").status_code == 400


def test_machine_free_after_end(client):
    sid = start(client)["session_id"]
    end(client, sid)
    start(client, "next@ybl")


# ---- phone Done ----

def test_phone_done_ends_and_pays(client):
    sid = start(client)["session_id"]
    deposit(client, sid, "can")
    res = client.post(f"/api/session/{sid}/end").get_json()
    assert res["status"] == "ended"
    assert res["end_reason"] == "user_done"
    assert res["total_amount"] == 2
    assert res["payout_ref"].startswith("MOCK_")

    # Machine sees it ended on its next poll
    poll = client.get(f"/api/machine/{MACHINE_ID}/session", headers=HEADERS).get_json()
    assert poll["active"] is False
    # And a late machine Done doesn't pay twice
    late = end(client, sid).get_json()
    assert late["already_ended"] is True
    assert late["payout_ref"] == res["payout_ref"]


def test_phone_done_unknown_session(client):
    assert client.post("/api/session/nope/end").status_code == 404


def test_machine_timeout_reason(client):
    sid = start(client)["session_id"]
    assert end(client, sid, reason="timeout").get_json()["end_reason"] == "timeout"


def test_page_has_live_counts_and_done(client):
    html = client.get(f"/m/{MACHINE_ID}").data
    for element_id in (b"welcome-view", b"Reverse Vending Machine", b"bottle-count", b"can-count",
                       b"total-amount", b"done-btn", b"rain"):
        assert element_id in html


# ---- timeout ----

def test_idle_session_times_out(client):
    client.application.config["SESSION_TIMEOUT_S"] = -1  # everything is already stale
    sid = start(client)["session_id"]
    res = client.get(f"/api/machine/{MACHINE_ID}/session", headers=HEADERS).get_json()
    assert res["active"] is False
    status = client.get(f"/api/session/{sid}").get_json()
    assert status["status"] == "ended"
    assert status["end_reason"] == "timeout"


def test_session_status_not_found(client):
    assert client.get("/api/session/nope").status_code == 404


# ---- admin ----

def admin_headers(password):
    """Basic auth header for user 'admin'."""
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_needs_password(client):
    res = client.get("/admin")
    assert res.status_code == 401
    assert "Basic" in res.headers["WWW-Authenticate"]
    assert client.get("/admin", headers=admin_headers("wrong")).status_code == 401


def test_admin_shows_history(client):
    sid = start(client, "test@ybl")["session_id"]
    deposit(client, sid, "can")
    deposit(client, sid, "bottle")
    ref = end(client, sid).get_json()["payout_ref"]

    res = client.get("/admin", headers=admin_headers("pw"))
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "test@ybl" in html
    assert ref in html
    assert "₹3" in html


def test_admin_disabled_without_password(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "ADMIN_PASSWORD": ""})
    assert app.test_client().get("/admin", headers=admin_headers("")).status_code == 403
