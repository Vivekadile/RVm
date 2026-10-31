// Smart RVM - ESP32 firmware
// Flow: IDLE (poll server) -> user starts session on phone -> ACTIVE
//       each item: detect -> POST deposit -> (only if accepted) sort + open gate
//       Done button / phone Done / 60 s no items -> session ends, server pays out -> IDLE
// Libraries: U8g2, ESP32Servo, ArduinoJson (v7). Pins: see docs/wiring.md

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <ESP32Servo.h>
#include "config.h"  // copy from config.h.example

// ---- Pins ----
#define PIN_IR        27  // FC-51, LOW = item present
#define PIN_INDUCTIVE 26  // LJ18A3 via 1N5819 + 10k pull-up, LOW = can
#define PIN_DONE_BTN  14  // INPUT_PULLUP, LOW = pressed
#define PIN_LED_GREEN 32
#define PIN_LED_RED   33
#define PIN_LED_YEL   23
#define PIN_BUZZER    25
#define PIN_GATE      18
#define PIN_SORTER    19

// ---- Servo angles ----
#define GATE_CLOSE  5
#define GATE_OPEN   90
#define SORT_CAN    45
#define SORT_BOTTLE 135
#define SORT_REST   90

// ---- Timings (ms) - slowed down on purpose so the demo is easy to follow ----
#define T_SHOW      2000   // show "CAN +Rs 2" before moving anything
#define T_SORT      1500   // sorter settle
#define T_OPEN      2500   // gate stays open
#define T_CLOSE     1500   // gate close settle
#define T_SETTLE    500    // wait after IR trigger before reading inductive
#define T_PAID      4000   // "Paid to UPI!" screen
#define T_POLL      2000   // server poll interval (idle and active)
#define T_IDLE_END  60000  // machine-side auto end with no items

// USE_HTTPS comes from config.h: 1 = public server (PythonAnywhere), 0 = laptop on local WiFi
#ifndef USE_HTTPS
#define USE_HTTPS 0
#endif
#if USE_HTTPS
#define HTTP_TIMEOUT_MS 10000  // TLS handshake over the internet is slow on ESP32
#else
#define HTTP_TIMEOUT_MS 4000
#endif

U8G2_SH1106_128X64_NONAME_F_4W_SW_SPI oled(U8G2_R0, 22, 21, 4, 17, 16);  // SCK, MOSI, CS, DC, RES
Servo gate;
Servo sorter;

// One client + HTTPClient for the whole run so the (TLS) connection is reused between polls
#if USE_HTTPS
WiFiClientSecure netClient;  // setInsecure() in setup: no cert check - OK for a prototype only
#else
WiFiClient netClient;
#endif
HTTPClient http;

enum State { IDLE, ACTIVE };
State state = IDLE;
String sessionId = "";
unsigned long lastPollMs = 0;
unsigned long lastItemMs = 0;
bool idleRejectShown = false;  // "Scan QR first" is on screen until IR clears
int lastIdleCode = 200;        // last idle poll result shown on OLED

// ---------- LEDs / buzzer / OLED ----------

void leds(bool green, bool red, bool yellow) {
  digitalWrite(PIN_LED_GREEN, green);
  digitalWrite(PIN_LED_RED, red);
  digitalWrite(PIN_LED_YEL, yellow);
}

// Bare 2-pin buzzer: DC pulse + 2 kHz tone (tested combo)
void beep() {
  digitalWrite(PIN_BUZZER, HIGH);
  delay(150);
  digitalWrite(PIN_BUZZER, LOW);
  tone(PIN_BUZZER, 2000, 150);
  delay(150);
  noTone(PIN_BUZZER);
  pinMode(PIN_BUZZER, OUTPUT);  // tone() takes the pin; give it back for digitalWrite
}

void beeps(int n) {
  for (int i = 0; i < n; i++) {
    beep();
    delay(100);
  }
}

// Big title line + up to 3 small lines
void show(const char* title, const char* l2 = "", const char* l3 = "", const char* l4 = "") {
  oled.clearBuffer();
  oled.setFont(u8g2_font_ncenB10_tr);
  oled.drawStr(0, 14, title);
  oled.setFont(u8g2_font_6x12_tf);
  oled.drawStr(0, 34, l2);
  oled.drawStr(0, 48, l3);
  oled.drawStr(0, 62, l4);
  oled.sendBuffer();
}

void showIdle() {
  show("Scan QR", "to start", "", MACHINE_ID);
}

void showSummary(int bottles, int cans, int total) {
  char l2[24], l3[24];
  snprintf(l2, sizeof(l2), "B:%d C:%d", bottles, cans);
  snprintf(l3, sizeof(l3), "Total Rs %d", total);
  show("Insert item", l2, l3, "DONE to finish");
}

// ---------- WiFi / HTTP ----------

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  show("WiFi...", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi OK, IP %s\n", WiFi.localIP().toString().c_str());
}

// Sends a request, parses the JSON reply into `out`. Returns HTTP code (<0 = network error).
int httpJson(const char* method, const String& path, const String& body, JsonDocument& out) {
  connectWifi();
  if (!http.begin(netClient, String(SERVER_URL) + path)) {
    Serial.printf("[HTTP] %s %s -> begin() failed\n", method, path.c_str());
    return -1;
  }
  http.addHeader("X-Machine-Key", MACHINE_KEY);
  http.addHeader("Content-Type", "application/json");

  int code = (strcmp(method, "GET") == 0) ? http.GET() : http.POST(body);
  if (code > 0) {
    String reply = http.getString();
    Serial.printf("[HTTP] %s %s -> %d %s\n", method, path.c_str(), code, reply.c_str());
    DeserializationError err = deserializeJson(out, reply);
    if (err) Serial.printf("JSON parse error: %s\n", err.c_str());
  } else {
    Serial.printf("[HTTP] %s %s -> %d (%s)\n", method, path.c_str(), code, http.errorToString(code).c_str());
  }
  http.end();
  return code;
}

// ---------- Session lifecycle ----------

// Gate servo is attached only while moving, so it doesn't buzz/jitter at rest.
void gateOpen() {
  gate.attach(PIN_GATE, 500, 2400);
  gate.write(GATE_OPEN);
}

void gateClose() {
  gate.write(GATE_CLOSE);
  delay(T_CLOSE);
  gate.detach();
}

void goIdle() {
  sorter.write(SORT_REST);
  leds(false, false, false);
  sessionId = "";
  state = IDLE;
  idleRejectShown = false;
  lastIdleCode = 200;
  showIdle();
}

// Shows the payout result from a session JSON, then returns to IDLE.
void showResult(JsonDocument& s) {
  const char* payout = s["payout_status"] | "";
  int total = s["total_amount"] | 0;
  char l2[24];
  snprintf(l2, sizeof(l2), "Rs %d", total);

  if (strcmp(payout, "success") == 0) {
    show("Paid to UPI!", l2, s["upi_masked"] | "", s["payout_ref"] | "");
    leds(true, false, false);
    beep();
  } else if (strcmp(payout, "skipped") == 0) {
    show("No items", "Nothing to pay");
    leds(false, false, false);
    beep();
  } else {
    // pending / failed: money is being processed (operator can refresh status on /admin)
    show("Payment", "processing", l2, s["payout_ref"] | "");
    leds(true, false, true);
    beep();
  }
  Serial.printf("Session %s ended: %s Rs %d\n", sessionId.c_str(), payout, total);
  delay(T_PAID);
  goIdle();
}

// Session was closed elsewhere (phone Done / server timeout): fetch and show result.
void handleEndedElsewhere() {
  JsonDocument res;
  int code = httpJson("GET", "/api/session/" + sessionId, "", res);
  if (code == 200) {
    showResult(res);
  } else {
    show("Session ended", "Scan QR again");
    delay(T_SHOW);
    goIdle();
  }
}

void startSession(const String& id) {
  sessionId = id;
  lastItemMs = millis();
  state = ACTIVE;
  Serial.printf("Session %s started\n", id.c_str());
  leds(false, false, true);
  beep();
  show("Welcome!", "Insert item", "", "DONE to finish");
}

void endSession(const char* reason) {
  show("Finishing...", "Sending payout");
  JsonDocument req, res;
  req["machine_id"] = MACHINE_ID;
  req["session_id"] = sessionId;
  req["reason"] = reason;
  String body;
  serializeJson(req, body);

  int code = -1;
  for (int attempt = 0; attempt < 3 && code != 200; attempt++) {
    code = httpJson("POST", "/api/machine/session/end", body, res);
    if (code != 200) delay(1000);
  }
  if (code == 200) {
    showResult(res);
  } else {
    show("Server error", "Session not closed", "Ask operator");
    leds(false, true, false);
    beeps(2);
    delay(T_PAID);
    goIdle();
  }
}

// Returns true if the server still has our session active.
bool checkSessionStillActive() {
  JsonDocument res;
  int code = httpJson("GET", String("/api/machine/") + MACHINE_ID + "/session", "", res);
  if (code != 200) return true;  // network blip: keep going
  return res["active"] && res["session_id"].as<String>() == sessionId;
}

// ---------- Sensors ----------

bool itemPresent() {
  if (digitalRead(PIN_IR) != LOW) return false;
  delay(50);
  return digitalRead(PIN_IR) == LOW;
}

void waitItemRemoved() {
  while (digitalRead(PIN_IR) == LOW) delay(20);
  delay(200);  // let it fully clear
}

// Majority vote over 7 reads of the inductive sensor.
bool isCan() {
  int lows = 0;
  for (int i = 0; i < 7; i++) {
    if (digitalRead(PIN_INDUCTIVE) == LOW) lows++;
    delay(20);
  }
  return lows >= 4;
}

bool donePressed() {
  if (digitalRead(PIN_DONE_BTN) != LOW) return false;
  delay(50);
  if (digitalRead(PIN_DONE_BTN) != LOW) return false;
  while (digitalRead(PIN_DONE_BTN) == LOW) delay(10);  // wait for release
  return true;
}

// ---------- Item handling ----------

// Item dropped with no session: warn once, don't move servos.
// Non-blocking so polling continues - if the user starts a session now, it is picked up.
void rejectNoSession() {
  idleRejectShown = true;
  leds(false, true, false);
  beeps(2);
  show("Scan QR first", "No active session", "Remove item");
}

// Shows why the idle poll failed, so a bad key / offline server is visible on the OLED.
void showIdleStatus(int code) {
  if (code == lastIdleCode || idleRejectShown) return;
  lastIdleCode = code;
  char l3[24];
  snprintf(l3, sizeof(l3), "HTTP %d", code);
  if (code == 200) showIdle();
  else if (code == 401) show("Key mismatch", "Fix MACHINE_KEY", "in config.h", l3);
  else if (code == 404) show("Unknown machine", "Fix MACHINE_ID", "in config.h", l3);
  else if (code < 0) show("Server offline", SERVER_URL, "Is backend running?", l3);
  else show("Server error", l3);
}

void rejectItem(const char* title, const char* reason) {
  leds(false, true, true);
  beeps(2);
  show(title, reason, "Remove item");
  waitItemRemoved();
  delay(T_SHOW);
  leds(false, false, true);
}

void handleItem() {
  delay(T_SETTLE);
  bool can = isCan();
  const char* type = can ? "can" : "bottle";
  Serial.printf("Item: %s\n", type);
  show(can ? "CAN?" : "BOTTLE?", "Checking...");

  JsonDocument req, res;
  req["machine_id"] = MACHINE_ID;
  req["session_id"] = sessionId;
  req["type"] = type;
  req["weight_g"] = nullptr;  // no load cell
  String body;
  serializeJson(req, body);
  int code = httpJson("POST", "/api/deposit", body, res);

  if (code == 429) { rejectItem("Limit reached", "Max items/session"); return; }
  if (code == 409 || code == 404) { handleEndedElsewhere(); return; }
  if (code != 201) { rejectItem("Rejected", code < 0 ? "Network error" : "Server error"); return; }

  // Accepted: confirm, then sort and drop
  char title[20];
  snprintf(title, sizeof(title), "%s +Rs %d", can ? "CAN" : "BOTTLE", (int)res["amount"]);
  show(title, "Accepted");
  leds(true, false, true);
  beep();
  delay(T_SHOW);

  sorter.write(can ? SORT_CAN : SORT_BOTTLE);
  show("Sorting...", can ? "-> can bin" : "-> bottle bin");
  delay(T_SORT);

  gateOpen();
  show("Gate open");
  delay(T_OPEN);
  gateClose();
  sorter.write(SORT_REST);

  leds(false, false, true);
  waitItemRemoved();
  showSummary(res["bottle_count"] | 0, res["can_count"] | 0, res["total_amount"] | 0);
}

// ---------- Arduino entry points ----------

// Stops with a clear OLED message if config.h is inconsistent, instead of failing silently later.
void checkConfig() {
  const char* problem = nullptr;
  bool isHttps = strncmp(SERVER_URL, "https://", 8) == 0;
  if (strstr(SERVER_URL, "USERNAME")) problem = "Set USERNAME in URL";
  else if (USE_HTTPS && !isHttps) problem = "USE_HTTPS=1 needs https";
  else if (!USE_HTTPS && isHttps) problem = "https URL: USE_HTTPS=1";
  if (!problem) return;
  Serial.printf("CONFIG ERROR: %s (SERVER_URL=%s, USE_HTTPS=%d)\n", problem, SERVER_URL, USE_HTTPS);
  show("Config error", problem, "Fix config.h", "and re-upload");
  leds(false, true, false);
  while (true) delay(1000);
}


void setup() {
  Serial.begin(115200);
  pinMode(PIN_IR, INPUT);
  pinMode(PIN_INDUCTIVE, INPUT);  // external 10k pull-up
  pinMode(PIN_DONE_BTN, INPUT_PULLUP);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_YEL, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  leds(false, false, false);

  oled.begin();
  gate.setPeriodHertz(50);
  sorter.setPeriodHertz(50);
  sorter.attach(PIN_SORTER, 500, 2400);

  // Park the gate closed, then release it
  gate.attach(PIN_GATE, 500, 2400);
  gate.write(GATE_CLOSE);
  delay(1000);
  gate.detach();

  checkConfig();
#if USE_HTTPS
  netClient.setInsecure();
#endif
  http.setReuse(true);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);

  connectWifi();
  Serial.printf("SERVER_URL=%s USE_HTTPS=%d MACHINE_ID=%s MACHINE_KEY=%.4s... (len %d)\n",
                SERVER_URL, USE_HTTPS, MACHINE_ID, MACHINE_KEY, (int)strlen(MACHINE_KEY));
  goIdle();
}

void loop() {
  unsigned long nowMs = millis();

  if (state == IDLE) {
    bool irLow = digitalRead(PIN_IR) == LOW;
    if (irLow && !idleRejectShown && itemPresent()) rejectNoSession();
    if (!irLow && idleRejectShown) {
      idleRejectShown = false;
      leds(false, false, false);
      lastIdleCode = 0;  // force a redraw on next poll
    }
    if (nowMs - lastPollMs >= T_POLL) {
      lastPollMs = nowMs;
      JsonDocument res;
      int code = httpJson("GET", String("/api/machine/") + MACHINE_ID + "/session", "", res);
      if (code == 200 && res["active"]) {
        idleRejectShown = false;
        lastIdleCode = 200;
        startSession(res["session_id"].as<String>());
      } else {
        showIdleStatus(code);
      }
    }
    return;
  }

  // ACTIVE
  if (donePressed()) { endSession("done_button"); return; }
  if (itemPresent()) {
    handleItem();
    lastItemMs = millis();
    return;
  }
  if (nowMs - lastItemMs >= T_IDLE_END) { endSession("timeout"); return; }
  if (nowMs - lastPollMs >= T_POLL) {
    lastPollMs = nowMs;
    if (!checkSessionStillActive()) handleEndedElsewhere();
  }
}
