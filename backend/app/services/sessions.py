"""Session helpers shared by machine and user routes."""
from flask import current_app

from ..db import get_db, now
from .payout import send_payout


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

    result = send_payout(
        session["upi_id"], session["total_amount"], session["id"],
        mode=current_app.config["PAYOUT_MODE"],
    )
    db.execute(
        "UPDATE sessions SET payout_status = ?, payout_ref = ? WHERE id = ?",
        (result["status"], result["ref"], session["id"]),
    )
    db.commit()
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
    }
