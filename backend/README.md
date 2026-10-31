# RVM Backend

Flask + SQLite backend for the Smart RVM. Payout is **mock only**.

## Run

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit MACHINE_KEY, SECRET_KEY
python run.py             # http://0.0.0.0:5001
python tools/make_qr.py   # printable QR -> ../docs/qr_RVM-01.png
```

SQLite tables are created automatically on first start (`DATABASE_PATH`, default `rvm.db`).

## Test

```bash
pytest -v
```

## Layout

```
app/
  __init__.py        create_app() factory
  config.py          settings from .env
  db.py              SQLite connection + schema
  routes/machine.py  ESP32 endpoints (X-Machine-Key)
  routes/user.py     phone page + session endpoints
  routes/admin.py    /admin transaction history
  services/payout.py mock payout
  services/sessions.py  shared session logic (end, timeout)
  templates/, static/   phone page
tools/make_qr.py     printable QR for a machine
```

## Endpoints

### Phone (no key)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/` | | health check |
| GET | `/m/<machine_id>` | | phone page (QR target) |
| POST | `/api/session/start` | `{machine_id, upi_id}` | `201` session; `409` if machine busy |
| GET | `/api/session/<session_id>` | | status, bottle/can counts, total, payout result |
| POST | `/api/session/<session_id>/end` | | phone Done button (reason `user_done`), same reply as machine end |

### Admin (HTTP Basic auth, user `admin`, password `ADMIN_PASSWORD`)

| Method | Path | Response |
|---|---|---|
| GET | `/admin` | transaction history page (auto-refresh 5 s); `403` if `ADMIN_PASSWORD` is empty |

### Machine (header `X-Machine-Key`)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/machine/<machine_id>/session` | | `{active: false}` or `{active: true, session_id, ...}` |
| POST | `/api/deposit` | `{machine_id, session_id, type: bottle\|can, weight_g?}` | `201` with amount, bottle/can counts, total; `409` if session ended; `429` limit reached |
| POST | `/api/machine/session/end` | `{machine_id, session_id, reason: done_button\|timeout\|error}` | session + `payout_status`, `payout_ref` |

## Rules the server enforces

- The amount is always computed from deposits on the server (`PRICE_BOTTLE`, `PRICE_CAN`), never taken from the client.
- One active session per machine (unique index).
- Ending a session is idempotent, so a repeated Done press can't pay twice.
- Sessions idle longer than `SESSION_TIMEOUT_S` are auto-ended (and paid) the next time the machine or phone polls.
- At most `MAX_ITEMS_PER_SESSION` deposits per session (default 20).
- A session with 0 items gets `payout_status: skipped`.
