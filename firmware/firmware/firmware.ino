// Final IR firmware for openAnime.
// Decodes NEC by capturing raw edge timings (pulseIn()/IRremote don't work on Zephyr).
//
// On the Uno Q the MCU does NOT reach Linux over a plain serial port -- the
// arduino-router daemon owns that link. Instead we send each button to the
// Linux side as a MessagePack-RPC notification over the Router Bridge:
//   Bridge.notify("ir_command", "UP");
// The Python backend (backend/app.py) registers "ir_command" with the router
// and receives these.
//
// Requires the "Arduino_RouterBridge" library (install via the Arduino IDE
// Library Manager, or it ships with the UNO Q board package).

#include <Arduino_RouterBridge.h>

#define IR_PIN A0
#define GAP_US 10000UL   // idle gap that marks the end of a frame

// --- Remote codes (recorded with ir_detect.ino) ---
#define HEX_UP    0xBF40FB04
#define HEX_DOWN  0xBE41FB04
#define HEX_LEFT  0xF807FB04
#define HEX_RIGHT 0xF906FB04
#define HEX_OK    0xBB44FB04
#define HEX_BACK  0xD728FB04
// --------------------------------------------------

void setup() {
    Bridge.begin();                    // connect to arduino-router (replaces Serial)
    pinMode(IR_PIN, INPUT_PULLUP);
}

uint32_t readNEC() {
    // Wait for the line to drop (start of a frame)
    while (digitalRead(IR_PIN) == HIGH) { /* idle */ }

    // Header: ~9ms low burst, ~4.5ms high space
    unsigned long t = micros();
    while (digitalRead(IR_PIN) == LOW) { if (micros() - t > GAP_US) return 0; }
    unsigned long lead = micros() - t;
    if (lead < 8000 || lead > 10000) return 0;

    t = micros();
    while (digitalRead(IR_PIN) == HIGH) { if (micros() - t > GAP_US) return 0; }
    unsigned long space = micros() - t;
    if (space < 3500 || space > 5500) return 0;

    // 32 data bits: 560us mark, then short space=0 / long space=1
    uint32_t data = 0;
    for (int i = 0; i < 32; i++) {
        t = micros();
        while (digitalRead(IR_PIN) == LOW) { if (micros() - t > GAP_US) return 0; }
        t = micros();
        while (digitalRead(IR_PIN) == HIGH) { if (micros() - t > GAP_US) break; }
        if (micros() - t > 1000) data |= (1UL << i);
    }
    return data;
}

void loop() {
    uint32_t code = readNEC();
    if (code == 0) return;

    const char* cmd = nullptr;
    if      (code == HEX_UP)    cmd = "UP";
    else if (code == HEX_DOWN)  cmd = "DOWN";
    else if (code == HEX_LEFT)  cmd = "LEFT";
    else if (code == HEX_RIGHT) cmd = "RIGHT";
    else if (code == HEX_OK)    cmd = "OK";
    else if (code == HEX_BACK)  cmd = "BACK";

    if (cmd != nullptr) {
        Bridge.notify("ir_command", cmd);   // push to the Linux backend
        Monitor.println(cmd);               // debug echo to the App Lab console
        delay(250);                         // debounce repeats
    }
}
