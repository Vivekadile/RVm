# Deploy on PythonAnywhere (free tier)

After this, anyone can scan the QR on mobile data and use the machine. The ESP32 talks to the server over HTTPS from any WiFi or hotspot.

Replace `USERNAME` everywhere with your PythonAnywhere username. Your site will be `https://USERNAME.pythonanywhere.com`.

## 1. Get the code and install (Bash console)

Go to **Consoles → Bash** and run:

```bash
git clone https://github.com/Vivekadile/RVm.git
cd RVm/backend
mkvirtualenv --python=/usr/bin/python3.12 rvm-venv     # use 3.10 or newer; remember the version
pip install -r requirements.txt
```

The virtualenv is created at `/home/USERNAME/.virtualenvs/rvm-venv`. To use it again in a new console, run `workon rvm-venv`.

## 2. Create `.env` on the server (never in git)

```bash
cd ~/RVm/backend
cp .env.example .env
python -c "import secrets; print('SECRET_KEY', secrets.token_hex(32)); print('MACHINE_KEY', secrets.token_hex(16)); print('ADMIN_PASSWORD', secrets.token_urlsafe(9))"
nano .env
```

In `nano`, set the values below. Save with Ctrl+O and Enter, then exit with Ctrl+X.

```
PUBLIC_BASE_URL=https://USERNAME.pythonanywhere.com
SECRET_KEY=<printed above>
MACHINE_KEY=<printed above>        # copy this into the ESP32 config.h too
ADMIN_PASSWORD=<printed above>
DATABASE_PATH=rvm.db               # becomes /home/USERNAME/RVm/backend/rvm.db
MACHINE_IDS=RVM-01
PAYOUT_MODE=mock
```

Ignore `HOST` and `PORT`; PythonAnywhere does not use them.

## 3. Initialise the database

```bash
cd ~/RVm/backend
workon rvm-venv
python -c "from app import create_app; create_app()"
ls -la rvm.db          # should exist now
pytest -q              # optional: all tests should pass
```

The tables are also created automatically every time the app starts.

## 4. Create the web app (Web tab)

1. **Web → Add a new web app → Next → Manual configuration**. Do *not* pick "Flask", because that generates its own app.
2. Pick the **same Python version** as the virtualenv (3.12 above), then click **Next**.
3. On the web app page, set:
   - **Source code:** `/home/USERNAME/RVm/backend`
   - **Working directory:** `/home/USERNAME/RVm/backend`
   - **Virtualenv:** `/home/USERNAME/.virtualenvs/rvm-venv`
4. Click the **WSGI configuration file** link (`/var/www/USERNAME_pythonanywhere_com_wsgi.py`). **Delete everything** in it and paste:

   ```python
   import sys

   project = "/home/USERNAME/RVm/backend"
   if project not in sys.path:
       sys.path.insert(0, project)

   # backend/wsgi.py builds the app with create_app() and reads backend/.env
   from wsgi import application  # noqa: F401
   ```

   Click **Save**.
5. **Static files** (optional, faster): URL `/static/` → Directory `/home/USERNAME/RVm/backend/app/static`
6. **Security → Force HTTPS:** turn it on.
7. Click the green **Reload** button.

## 5. Check it

- `https://USERNAME.pythonanywhere.com/` should return `{"ok": true, "service": "rvm-backend"}`
- `https://USERNAME.pythonanywhere.com/m/RVM-01` should show the welcome page with the falling cans and bottles
- `https://USERNAME.pythonanywhere.com/admin` should ask for login; use `admin` and your `ADMIN_PASSWORD`
- If something fails, check **Web → Log files → Error log**. The **Server log** shows the `Database:` path on startup.

## 6. Printed QR for the public URL

On your laptop, run:

```bash
cd backend && source venv/bin/activate
python tools/make_qr.py --base-url https://USERNAME.pythonanywhere.com
```

This writes `docs/qr_RVM-01.png`. Print it.

## 7. ESP32

In `firmware/rvm_esp32/config.h`:

```c
#define USE_HTTPS   1
...
  #define SERVER_URL "https://USERNAME.pythonanywhere.com"
...
#define MACHINE_KEY "<same MACHINE_KEY as the server .env>"
```

The ESP32 can now use any WiFi or phone hotspot that has internet; it no longer needs to be on the laptop's network. Upload the sketch. For local testing again, set `USE_HTTPS 0`.

## Updating the server later

```bash
cd ~/RVm && git pull
```

Then click **Reload** on the Web tab.

## Free-tier notes

- The free web app stops after **3 months** unless you click **"Run until 3 months from today"** on the Web tab.
- Free accounts get one web worker, which is fine for one machine and a few phones.
- `setInsecure()` on the ESP32 skips certificate checks. That is acceptable for a prototype, not for real money.
