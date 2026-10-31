"""Endpoints called by the ESP32 (need X-Machine-Key)."""
import hmac
from functools import wraps

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db, now
from ..services.sessions import (
    count_by_type, end_session, expire_stale, get_active_session, get_session, session_to_dict,
)

bp = Blueprint("machine", __name__)

VALID_TYPES = ("bottle", "can")
VALID_END_REASONS = ("done_button", "timeout", "error")


def error(message, status):
    """JSON error response."""
    return jsonify({"ok": False, "error": message}), status


def require_machine_key(view):
    """Reject requests without the correct X-Machine-Key header."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        expected = current_app.config["MACHINE_KEY"]
        given = request.headers.get("X-Machine-Key", "")
        if not expected or not hmac.compare_digest(given, expected):
            return error("invalid machine key", 401)
        return view(*args, **kwargs)
    return wrapper


def known_machine(machine_id):
    """True if machine_id is listed in MACHINE_IDS."""
    return machine_id in current_app.config["MACHINE_IDS"]


def load_machine_session(data):
    """Validate machine_id + session_id from a body; returns (session, error_response)."""
    machine_id = data.get("machine_id")
    session_id = data.get("session_id")
    if not known_machine(machine_id):
        return None, error("unknown machine", 404)
    session = get_session(session_id) if session_id else None
    if session is None or session["machine_id"] != machine_id:
        return None, error("session not found", 404)
    return session, None


@bp.get("/api/machine/<machine_id>/session")
@require_machine_key
def poll_session(machine_id):
    """ESP32 polls this to know if a user has started a session."""
    if not known_machine(machine_id):
        return error("unknown machine", 404)
    expire_stale(machine_id)
    session = get_active_session(machine_id)
    if session is None:
        return jsonify({"ok": True, "active": False})
    return jsonify({"ok": True, "active": True, **session_to_dict(session)})


@bp.post("/api/deposit")
@require_machine_key
def deposit():
    """Record one bottle/can for the active session."""
    data = request.get_json(silent=True) or {}
    session, err = load_machine_session(data)
    if err:
        return err
    if session["status"] != "active":
        return error("session not active", 409)

    item_type = data.get("type")
    if item_type not in VALID_TYPES:
        return error("type must be 'bottle' or 'can'", 400)

    # weight_g is optional (no load cell yet)
    weight_g = data.get("weight_g")
    if weight_g is not None:
        try:
            weight_g = float(weight_g)
        except (TypeError, ValueError):
            return error("weight_g must be a number", 400)

    cfg = current_app.config
    if session["item_count"] >= cfg["MAX_ITEMS_PER_SESSION"]:
        return error("limit reached", 429)

    amount = cfg["PRICE_CAN"] if item_type == "can" else cfg["PRICE_BOTTLE"]
    ts = now()
    db = get_db()
    cur = db.execute(
        "INSERT INTO deposits (session_id, machine_id, type, weight_g, amount, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session["id"], session["machine_id"], item_type, weight_g, amount, ts),
    )
    db.execute(
        "UPDATE sessions SET item_count = item_count + 1, total_amount = total_amount + ?, "
        "last_activity_at = ? WHERE id = ?",
        (amount, ts, session["id"]),
    )
    db.commit()

    session = get_session(session["id"])
    bottle_count, can_count = count_by_type(session["id"])
    return jsonify({
        "ok": True,
        "deposit_id": cur.lastrowid,
        "type": item_type,
        "amount": amount,
        "item_count": session["item_count"],
        "bottle_count": bottle_count,
        "can_count": can_count,
        "total_amount": session["total_amount"],
    }), 201


@bp.post("/api/machine/session/end")
@require_machine_key
def end():
    """Done button: close the session and trigger payout (idempotent)."""
    data = request.get_json(silent=True) or {}
    session, err = load_machine_session(data)
    if err:
        return err

    reason = data.get("reason", "done_button")
    if reason not in VALID_END_REASONS:
        return error(f"reason must be one of {', '.join(VALID_END_REASONS)}", 400)

    already_ended = session["status"] != "active"
    session = end_session(session, reason)
    return jsonify({"ok": True, "already_ended": already_ended, **session_to_dict(session)})
