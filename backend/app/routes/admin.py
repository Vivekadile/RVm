"""Operator page: transaction history (HTTP Basic auth, user 'admin')."""
import hmac
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, current_app, redirect, render_template, request, url_for

from ..db import get_db
from ..services.sessions import get_session, refresh_payout

bp = Blueprint("admin", __name__)


def require_admin(view):
    """Ask for ADMIN_PASSWORD via the browser's login popup."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        expected = current_app.config["ADMIN_PASSWORD"]
        if not expected:
            return Response("Admin disabled: set ADMIN_PASSWORD in .env", 403)
        auth = request.authorization
        given = auth.password if auth and auth.username == "admin" else ""
        if not hmac.compare_digest(given or "", expected):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="RVM admin"'})
        return view(*args, **kwargs)
    return wrapper


def fmt_time(ts):
    """Unix seconds -> 'DD Mon HH:MM:SS' local time."""
    return datetime.fromtimestamp(ts).strftime("%d %b %H:%M:%S") if ts else "-"


@bp.get("/admin")
@require_admin
def history():
    """All sessions (newest first) with their deposits and overall totals."""
    db = get_db()
    sessions = db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 200").fetchall()

    deposits = {}
    for row in db.execute("SELECT * FROM deposits ORDER BY created_at"):
        deposits.setdefault(row["session_id"], []).append(row)

    totals = db.execute(
        "SELECT COUNT(*) AS sessions, "
        "COALESCE(SUM(status = 'active'), 0) AS active, "
        "COALESCE(SUM(payout_status = 'success'), 0) AS paid, "
        "COALESCE(SUM(payout_status IN ('pending', 'failed') AND provider = 'cashfree'), 0) AS unsettled, "
        "COALESCE(SUM(CASE WHEN payout_status = 'success' THEN total_amount END), 0) AS paid_amount "
        "FROM sessions"
    ).fetchone()
    items = db.execute(
        "SELECT COALESCE(SUM(type = 'bottle'), 0) AS bottles, COALESCE(SUM(type = 'can'), 0) AS cans FROM deposits"
    ).fetchone()

    cfg = current_app.config
    payout_label = f"Cashfree {cfg['CASHFREE_ENV']}" if cfg["PAYOUT_MODE"] == "cashfree" else "mock"
    return render_template(
        "admin.html", sessions=sessions, deposits=deposits, totals=totals, items=items, fmt_time=fmt_time,
        payout_label=payout_label, needs_refresh=needs_refresh,
    )


def needs_refresh(session):
    """True for Cashfree payouts that aren't final yet (Refresh status button)."""
    return session["provider"] == "cashfree" and session["payout_status"] in ("pending", "failed")


@bp.post("/admin/refresh/<session_id>")
@require_admin
def refresh_one(session_id):
    """Re-check one session's Cashfree transfer status."""
    session = get_session(session_id)
    if session is not None:
        refresh_payout(session)
    return redirect(url_for("admin.history"))


@bp.post("/admin/refresh")
@require_admin
def refresh_all():
    """Re-check every Cashfree payout that isn't final yet."""
    rows = get_db().execute(
        "SELECT * FROM sessions WHERE provider = 'cashfree' AND payout_status IN ('pending', 'failed') "
        "ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    for session in rows:
        refresh_payout(session)
    return redirect(url_for("admin.history"))
