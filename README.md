# Smart RVM (Reverse Vending Machine) - Prototype

College demo of a breadboard reverse vending machine. The user drops a plastic bottle or an aluminium can, the machine identifies and sorts it, and a reward goes to the user's UPI ID: **Rs 1 per bottle, Rs 2 per can**. Payout is a **mock** (no real money).

## User flow

1. **Idle.** The OLED shows "Scan QR to start". A printed QR on the front opens `http://<laptop-ip>:5001/m/RVM-01`. If someone drops an item with no session, the red LED lights, the buzzer beeps twice, the OLED shows "Scan QR first", and the gate stays shut.
2. **Start.** On the phone, the user enters a UPI ID and taps **Start**. If someone else is already using the machine, the phone shows "Machine in use".
3. **Session picked up.** The ESP32 polls `GET /api/machine/RVM-01/session` every 2 s. When it finds the session, it beeps, turns the yellow LED on, and shows "Welcome! Insert item". The phone shows live bottles, cans and total, refreshed every 2 s.
4. **Each item.** The IR sensor detects the item. After 500 ms the inductive sensor decides can or bottle, and the ESP32 sends `POST /api/deposit`.
   - **Accepted:** the OLED shows "CAN +Rs 2", with green LED and a beep. Then the sorter turns, the gate opens for 2.5 s and closes, and the OLED shows "B:x C:y Total Rs z".
   - **Rejected or limit reached:** red LED, 2 beeps, and the gate stays shut.
5. **Done.** The user presses the machine's Done button or Done on the phone. The server totals the deposits and makes a mock payout. The OLED shows "Paid to UPI! Rs X" for 4 s, and the phone shows "Rs X sent to ab\*\*\*\*@upi, ref MOCK_...". The machine then goes back to idle. With no item for 60 s, the machine ends the session by itself. The server also auto-ends after 300 s idle, as a backup.

```
rvm-project/
├── backend/     Flask + SQLite API and phone page   (see backend/README.md)
├── firmware/    ESP32 Arduino sketch
└── docs/        wiring.md (pin table), qr_RVM-01.png (printable QR)
```

## Hardware

- ESP32 DevKit V1 (USB power)
- 1.3" SPI OLED, SH1106
- FC-51 IR obstacle sensor (item detect)
- LJ18A3 inductive proximity sensor on 9V, plus a 1N5819 diode and 10k pull-up (can detect)
- 2x SG90 servo on a 5V adapter (gate and sorter)
- LEDs: green, red, yellow; buzzer; Done push button
- Cardboard body with slot, chute, gate, sorter flap and 2 bins

Full pin table: [docs/wiring.md](docs/wiring.md)

## Setup

### 1. Network

The laptop, the ESP32 and the phone must be on the **same WiFi**. A phone hotspot is easiest, because college WiFi usually blocks devices from talking to each other. Find the laptop's LAN IP:

```bash
ipconfig getifaddr en0      # macOS
hostname -I                 # Linux
ipconfig                    # Windows
```

### 2. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # set MACHINE_KEY and SECRET_KEY
python run.py                     # listens on 0.0.0.0:5001
pytest -v                         # optional: run tests
```

Port 5001 is used because macOS AirPlay Receiver takes 5000. To check it works, open `http://<laptop-ip>:5001/m/RVM-01` on your phone.

### Transaction history (admin)

Open `http://<laptop-ip>:5001/admin` and log in with username `admin` and the `ADMIN_PASSWORD` from `backend/.env`. The page shows totals (sessions, payouts, ₹ paid, bottles, cans) and every session with its UPI ID, items, amount, who ended it, and the payout ref. It refreshes every 5 s.

### 3. Printed QR

```bash
cd backend
python tools/make_qr.py               # auto-detects the LAN IP
python tools/make_qr.py --ip 192.168.1.4   # or set it yourself
```

This writes `docs/qr_RVM-01.png` with "Scan to recycle" and the URL underneath. Print it and stick it on the front of the machine. **Make the QR again if the laptop's IP changes**, for example after switching WiFi or hotspot.

### 4. ESP32 firmware

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. Add the ESP32 boards: go to *Preferences → Additional Board URLs* and add `https://espressif.github.io/arduino-esp32/package_esp32_index.json`. Then go to *Boards Manager* and install **esp32 by Espressif**.
3. In *Library Manager*, install **U8g2**, **ESP32Servo** and **ArduinoJson** (v7).
4. **Create `config.h`.** The sketch won't compile without it. From the project root:
   ```bash
   cp firmware/rvm_esp32/config.h.example firmware/rvm_esp32/config.h
   ```
   Open `firmware/rvm_esp32/config.h` and set:
   ```c
   #define WIFI_SSID   "your-hotspot-name"      // same WiFi as the laptop and phone
   #define WIFI_PASS   "your-hotspot-password"
   #define SERVER_URL  "http://192.168.1.4:5001" // laptop LAN IP + port, not localhost
   #define MACHINE_ID  "RVM-01"
   #define MACHINE_KEY "..."                     // copy MACHINE_KEY from backend/.env
   ```
   `config.h` is gitignored, so never commit it. Arduino IDE will show it as a second tab next to the sketch.
5. Open `firmware/rvm_esp32/rvm_esp32.ino`. Select board **ESP32 Dev Module** and the right port, then click **Upload**. If the upload hangs on "Connecting...", hold the **BOOT** button.
6. Open the Serial Monitor at 115200 baud. You should see `WiFi OK, IP ...`, and the OLED should show "Scan QR to start".

## Secrets

`backend/.env` and `firmware/rvm_esp32/config.h` hold the real values. Both are in `.gitignore`. Only the `.example` files go into the repo.

## Limitations (prototype)

- Payout is mocked (`PAYOUT_MODE=mock`).
- There is no load cell, so `weight_g` is sent as `null`.
- HTTP only, on the local network. Not meant for deployment.
