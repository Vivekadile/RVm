"""Phone-facing page and endpoints."""
import re
import sqlite3
import uuid

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from ..db import get_db, now
from ..services.sessions import end_session, expire_stale, get_session, session_to_dict

bp = Blueprint("user", __name__)

# UPI handles are a single word (ybl, okicici, paytm, upi) - no dots, so emails like x@gmail.com fail
UPI_RE = re.compile(r"^[a-zA-Z0-9._-]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$")


def error(message, status):
    """JSON error response."""
    return jsonify({"ok": False, "error": message}), status


@bp.get("/")
def health():
    """Health check."""
    return jsonify({"ok": True, "service": "rvm-backend"})


@bp.get("/m/<machine_id>")
def machine_page(machine_id):
    """Page the user lands on after scanning the machine's QR."""
    if machine_id not in current_app.config["MACHINE_IDS"]:
        abort(404)
    return render_template(
        "machine.html",
        machine_id=machine_id,
        price_bottle=current_app.config["PRICE_BOTTLE"],
        price_can=current_app.config["PRICE_CAN"],
    )


@bp.post("/api/session/start")
def start_session():
    """Start a session for a machine with the user's UPI ID."""
    data = request.get_json(silent=True) or {}
    machine_id = data.get("machine_id")
    upi_id = (data.get("upi_id") or "").strip()

    if machine_id not in current_app.config["MACHINE_IDS"]:
        return error("unknown machine", 404)
    if not UPI_RE.match(upi_id):
        return error("invalid UPI ID", 400)

    expire_stale(machine_id)
    session_id = uuid.uuid4().hex
    ts = now()
    db = get_db()
    try:
        db.execute(
            "INSERT INTO sessions (id, machine_id, upi_id, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, machine_id, upi_id, ts, ts),
        )
        db.commit()
    except sqlite3.IntegrityError:
        # Unique index: machine already has an active session
        return error("Machine in use, try again in a moment", 409)

    return jsonify({"ok": True, **session_to_dict(get_session(session_id))}), 201


@bp.get("/api/session/<session_id>")
def session_status(session_id):
    """Phone polls this to show live item count / payout result."""
    session = get_session(session_id)
    if session is None:
        return error("session not found", 404)
    if session["status"] == "active":
        expire_stale(session["machine_id"])
        session = get_session(session_id)
    return jsonify({"ok": True, **session_to_dict(session)})


@bp.post("/api/session/<session_id>/end")
def end_session_from_phone(session_id):
    """Phone Done button: close the session and pay out (idempotent)."""
    session = get_session(session_id)
    if session is None:
        return error("session not found", 404)
    already_ended = session["status"] != "active"
    session = end_session(session, "user_done")
    return jsonify({"ok": True, "already_ended": already_ended, **session_to_dict(session)})
