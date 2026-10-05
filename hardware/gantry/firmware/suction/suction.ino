// Divinci Table — suction head for the camera gantry (hardware/gantry/README.md).
// Arduino Nano. Plain-text commands over USB serial at 115200 baud, one per line; every reply ends "ok" or "error: …".
//
//   PICK          pump on, release valve closed: the cup grips
//   DROP          pump off, valve open for 300 ms, then closed: the card lets go at once
//   TURN <deg>    servo to 0..180 degrees (90 = cup straight; 0/180 = a quarter turn either way, for tapping)
//   OFF           pump off, valve closed (the safe state)
//   STATUS        "pump=0|1 valve=0|1 turn=<deg>"
//
// Safety: the pump never runs more than PUMP_MAX_MS at a time, and everything goes OFF if the computer is silent for
// SILENCE_MS (a crashed script can't leave the pump running). The printer itself is driven separately by gantry.py.
#include <Servo.h>

const uint8_t PUMP_PIN = 5;            // MOSFET module A, TRIG/PWM
const uint8_t VALVE_PIN = 6;           // MOSFET module B, TRIG/PWM
const uint8_t SERVO_PIN = 9;           // servo signal (servo power comes from the 5 V buck, not the Nano)
const unsigned long PUMP_MAX_MS = 60000;
const unsigned long SILENCE_MS = 120000;
const unsigned long RELEASE_MS = 300;

Servo turn;
bool pumpOn = false, valveOpen = false;
int angle = 90;
unsigned long pumpSince = 0, lastHeard = 0;
String line;

void setPump(bool on) { pumpOn = on; digitalWrite(PUMP_PIN, on ? HIGH : LOW); if (on) pumpSince = millis(); }
void setValve(bool open) { valveOpen = open; digitalWrite(VALVE_PIN, open ? HIGH : LOW); }
void safeOff() { setPump(false); setValve(false); }

void setup() {
  pinMode(PUMP_PIN, OUTPUT); pinMode(VALVE_PIN, OUTPUT);
  safeOff();
  turn.attach(SERVO_PIN); turn.write(angle);
  Serial.begin(115200);
  lastHeard = millis();
  Serial.println("suction-head ready");
}

void handle(String cmd) {
  cmd.trim(); cmd.toUpperCase();
  if (cmd == "PICK") { setValve(false); setPump(true); Serial.println("ok"); }
  else if (cmd == "DROP") { setPump(false); setValve(true); delay(RELEASE_MS); setValve(false); Serial.println("ok"); }
  else if (cmd == "OFF") { safeOff(); Serial.println("ok"); }
  else if (cmd.startsWith("TURN ")) {
    int a = cmd.substring(5).toInt();
    if (a < 0 || a > 180) { Serial.println("error: TURN takes 0..180"); return; }
    angle = a; turn.write(angle); Serial.println("ok");
  }
  else if (cmd == "STATUS") {
    Serial.print("pump="); Serial.print(pumpOn); Serial.print(" valve="); Serial.print(valveOpen);
    Serial.print(" turn="); Serial.println(angle); Serial.println("ok");
  }
  else if (cmd.length()) { Serial.println("error: unknown command"); }
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    lastHeard = millis();
    if (c == '\n' || c == '\r') { if (line.length()) handle(line); line = ""; }
    else if (line.length() < 32) line += c;
  }
  if (pumpOn && millis() - pumpSince > PUMP_MAX_MS) { safeOff(); Serial.println("error: pump ran too long — switched off"); }
  if (millis() - lastHeard > SILENCE_MS && (pumpOn || valveOpen)) { safeOff(); Serial.println("error: no commands for 2 min — switched off"); }
}
