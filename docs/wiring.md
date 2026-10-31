# Wiring

ESP32 DevKit V1. All grounds (ESP32, 5V servo adapter, 9V sensor supply) must be **common**.

## Pin table

| Part | Part pin | ESP32 GPIO | Notes |
|---|---|---|---|
| OLED 1.3" SH1106 (SPI) | SCK / CLK | 22 | U8g2 software SPI |
| | MOSI / SDA | 21 | |
| | CS | 4 | |
| | DC | 17 | |
| | RES | 16 | |
| | VCC / GND | 3V3 / GND | |
| FC-51 IR sensor | OUT | 27 | LOW = item present |
| | VCC / GND | 3V3 / GND | |
| LJ18A3 inductive sensor | Signal (black) | 26 | Via 1N5819 (cathode to sensor) + 10k pull-up to 3V3. LOW = metal (can) |
| | Brown / Blue | 9V / GND | Separate 9V supply, common GND |
| SG90 gate servo | Signal (orange) | 18 | Closed 5°, open 90°; attached only while moving |
| SG90 sorter servo | Signal (orange) | 19 | Can 45°, bottle 135°, rest 90° |
| Both servos | Red / Brown | 5V adapter / GND | Not from ESP32 3V3 |
| Green LED | Anode (via resistor) | 32 | Item accepted / payout OK |
| Red LED | Anode (via resistor) | 33 | Error |
| Yellow LED | Anode (via resistor) | 23 | On while a session is active |
| Buzzer | + | 25 | |
| Done button | One leg | 14 | Other leg to GND, `INPUT_PULLUP` |

## LED / buzzer meaning

| Signal | Meaning |
|---|---|
| All off | Idle, waiting for a session |
| Yellow on (steady) | Session active |
| Green + 1 beep | Item accepted / payout done |
| Red + 2 beeps | Item rejected (no session, limit reached, network/server error) |

Beep = `digitalWrite(25, HIGH)` 150 ms, then `tone(25, 2000, 150)` (bare 2-pin buzzer).

## Timings (firmware `#define`s)

| Name | ms | What |
|---|---|---|
| `T_SETTLE` | 500 | wait after IR before reading inductive |
| `T_SHOW` | 2000 | show "CAN +Rs 2" before moving |
| `T_SORT` | 1500 | sorter settle |
| `T_OPEN` | 2500 | gate open |
| `T_CLOSE` | 1500 | gate close settle |
| `T_PAID` | 4000 | "Paid to UPI!" screen |
| `T_IDLE_END` | 60000 | auto end with no items |
