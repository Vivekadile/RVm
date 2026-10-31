"""Session helpers shared by machine and user routes."""
from flask import current_app

from ..db import get_db, now
from .payout import cashfree_status, send_payout


def get_session(session_id):
    """Fetch one session row, or None."""
    return get_db().execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def get_active_session(machine_id):
    """Fetch the active session for a machine, or None."""
    return get_db().execute(
        "SELECT * FROM sessions WHERE machine_id = ? AND status = 'active'", (machine_id,)
    ).fetchone()


def end_session(session, reason):
    """Close an active session and pay out its total; returns the updated row."""
    db = get_db()
    # Guard on status so a double "end" can't pay twice
    cur = db.execute(
        "UPDATE sessions SET status = 'ended', end_reason = ?, ended_at = ? "
        "WHERE id = ? AND status = 'active'",
        (reason, now(), session["id"]),
    )
    db.commit()
    if cur.rowcount == 0:
        return get_session(session["id"])

    result = send_payout(session["upi_id"], session["total_amount"], session["id"], current_app.config)
    save_payout(session["id"], result)
    return get_session(session["id"])


def save_payout(session_id, result):
    """Store a payout result dict on the session row."""
    db = get_db()
    db.execute(
        "UPDATE sessions SET payout_status = ?, payout_ref = COALESCE(?, payout_ref), provider = ?, "
        "provider_ref = COALESCE(?, provider_ref), provider_status = ?, provider_error = ? WHERE id = ?",
        (result["status"], result["ref"], result["provider"], result["provider_ref"],
         result["provider_status"], result["error"], session_id),
    )
    db.commit()


def refresh_payout(session):
    """Re-check a Cashfree payout that isn't final yet; returns the updated row."""
    if session["provider"] != "cashfree" or session["payout_status"] == "success" or not session["payout_ref"]:
        return session
    result = cashfree_status(session["payout_ref"], current_app.config)
    if result["provider_status"] == "ERROR" and session["provider_status"] not in (None, "ERROR"):
        # Couldn't reach Cashfree this time: keep the last known status, just note the error
        get_db().execute("UPDATE sessions SET provider_error = ? WHERE id = ?", (result["error"], session["id"]))
        get_db().commit()
    else:
        save_payout(session["id"], result)
    return get_session(session["id"])


def expire_stale(machine_id):
    """End the machine's active session if it has been idle too long."""
    session = get_active_session(machine_id)
    timeout = current_app.config["SESSION_TIMEOUT_S"]
    if session and now() - session["last_activity_at"] > timeout:
        end_session(session, "timeout")


def mask_upi(upi_id):
    """Mask a UPI ID for display, e.g. abcd@ybl -> ab****@ybl."""
    name, _, bank = upi_id.partition("@")
    return f"{name[:2]}****@{bank}"


def count_by_type(session_id):
    """Return (bottle_count, can_count) for a session."""
    rows = get_db().execute(
        "SELECT type, COUNT(*) AS n FROM deposits WHERE session_id = ? GROUP BY type", (session_id,)
    ).fetchall()
    counts = {row["type"]: row["n"] for row in rows}
    return counts.get("bottle", 0), counts.get("can", 0)


def session_to_dict(session):
    """Public JSON view of a session (UPI masked)."""
    bottle_count, can_count = count_by_type(session["id"])
    return {
        "session_id": session["id"],
        "machine_id": session["machine_id"],
        "status": session["status"],
        "upi_masked": mask_upi(session["upi_id"]),
        "item_count": session["item_count"],
        "bottle_count": bottle_count,
        "can_count": can_count,
        "total_amount": session["total_amount"],
        "end_reason": session["end_reason"],
        "payout_status": session["payout_status"],
        "payout_ref": session["payout_ref"],
        "provider": session["provider"],
        "provider_ref": session["provider_ref"],
        "provider_status": session["provider_status"],
    }
