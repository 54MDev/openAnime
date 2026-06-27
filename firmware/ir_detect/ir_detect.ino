// Step 1 sketch — find your remote's hex codes.
// Open Serial Monitor at 9600 baud, press each button, record the HEX values.
// Decodes NEC by capturing raw edge timings (pulseIn() is broken on Zephyr).

#define IR_PIN A0
#define GAP_US 10000UL   // idle gap that marks the end of a frame

void setup() {
    Serial.begin(9600);
    pinMode(IR_PIN, INPUT_PULLUP);
    Serial.println("IR detector ready — press a button");
}

void loop() {
    // Wait for the line to drop (start of a frame)
    while (digitalRead(IR_PIN) == HIGH) { /* idle */ }

    // --- Header: ~9ms low burst, ~4.5ms high space ---
    unsigned long t = micros();
    while (digitalRead(IR_PIN) == LOW) { if (micros() - t > GAP_US) return; }
    unsigned long lead = micros() - t;
    if (lead < 8000 || lead > 10000) return;        // not a NEC header

    t = micros();
    while (digitalRead(IR_PIN) == HIGH) { if (micros() - t > GAP_US) return; }
    unsigned long space = micros() - t;
    if (space < 3500 || space > 5500) return;

    // --- 32 data bits: 560us mark, then short space=0 / long space=1 ---
    uint32_t data = 0;
    for (int i = 0; i < 32; i++) {
        // mark (low)
        t = micros();
        while (digitalRead(IR_PIN) == LOW) { if (micros() - t > GAP_US) return; }
        // space (high) — its length encodes the bit
        t = micros();
        while (digitalRead(IR_PIN) == HIGH) { if (micros() - t > GAP_US) break; }
        if (micros() - t > 1000) data |= (1UL << i);
    }

    Serial.print("HEX: 0x");
    Serial.println(data, HEX);
    delay(250);   // debounce repeats
}
