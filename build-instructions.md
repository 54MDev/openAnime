# Build Instructions

## Prerequisites

Before starting, you need:
- Arduino Uno Q with Debian Linux booted and SSH or keyboard/monitor access
- KY-022 IR receiver wired to the STM32 MCU header (Signal → PA0, VCC → 3.3V, GND → GND)
- USB-C audio adapter plugged into the hub's USB-A port
- USB-C hub connected (HDMI to TV, PD in for power)
- Internet connection (Wi-Fi configured or Ethernet dongle)
- This repo cloned to `/home/user/openAnime/` on the Uno Q

---

## Phase 1: Flash the Firmware

### 1.1 Install Arduino IDE on your dev machine (not the Uno Q)

Download from [arduino.cc/en/software](https://www.arduino.cc/en/software). The Uno Q's STM32 is programmed from a host computer via USB, not from the Linux side directly.

### 1.2 Add the STM32 board package

In Arduino IDE → Preferences → Additional Board Manager URLs, add:
```
https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json
```
Then: Tools → Board → Board Manager → search "STM32" → install "STM32 MCU based boards".

### 1.3 Install IRremote library

Sketch → Include Library → Manage Libraries → search "IRremote" → install version 4.x by shirriff/z3t0/ArminJo.

### 1.4 Run the detection sketch first

Before uploading the final firmware, upload a raw detection sketch to find your remote's hex codes:

```cpp
#include <IRremote.hpp>
#define IR_RECEIVE_PIN PA0

void setup() {
    Serial.begin(9600);
    IrReceiver.begin(IR_RECEIVE_PIN, ENABLE_LED_FEEDBACK);
}

void loop() {
    if (IrReceiver.decode()) {
        Serial.print("Protocol: ");
        Serial.print(IrReceiver.decodedIRData.protocol);
        Serial.print("  HEX: 0x");
        Serial.println(IrReceiver.decodedIRData.decodedRawData, HEX);
        IrReceiver.resume();
    }
}
```

Open Serial Monitor at 9600 baud, press each button, and record:

| Button | Hex Code |
|--------|----------|
| UP | `0x____` |
| DOWN | `0x____` |
| LEFT | `0x____` |
| RIGHT | `0x____` |
| OK / Enter | `0x____` |
| BACK | `0x____` |

### 1.5 Upload final firmware

Open `firmware/firmware.ino`, replace the placeholder hex values at the top with your recorded codes, select the correct STM32 board and port, and upload.

**Verify:** Open Serial Monitor, press buttons — you should see `UP`, `DOWN`, etc. printed cleanly.

---

## Phase 2: Set Up the Linux Environment

All commands below run on the Uno Q's Debian terminal.

### 2.1 Update system

```bash
sudo apt update && sudo apt upgrade -y
```

### 2.2 Install system dependencies

```bash
sudo apt install -y \
    python3 python3-pip \
    chromium \
    openbox \
    x11-xserver-utils \
    xorg \
    mpv \
    wmctrl \
    unclutter \
    lightdm
```

### 2.3 Install Python dependencies

```bash
pip3 install pyserial websockets
```

### 2.4 Install yt-dlp

```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
```

Keep yt-dlp updated regularly — streaming sites change often:
```bash
yt-dlp -U
```

---

## Phase 3: Configure Audio

### 3.1 Find your USB audio device

Plug in the USB audio adapter, then:
```bash
aplay -l
```

Look for a line like `card 1: Device [USB Audio Device]`. Note the card number.

### 3.2 Set USB audio as default

Create or edit `/etc/asound.conf`:
```
defaults.pcm.card 1
defaults.ctl.card 1
```
Replace `1` with your actual card number.

### 3.3 Test audio

```bash
speaker-test -t wav -c 2
```

You should hear audio from your USB device. If it cuts out after a few minutes, this is a known Uno Q USB driver issue — use Bluetooth audio instead.

---

## Phase 4: Deploy the Application

### 4.1 Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/openAnime.git /home/user/openAnime
```

### 4.2 Find the serial bridge device

The STM32 communicates with Linux over an internal serial bridge. Find the device name:
```bash
ls /dev/tty*
```

Common names: `/dev/ttyS0`, `/dev/ttyAMA0`, `/dev/ttyUSB0`. If unsure, check:
```bash
dmesg | grep tty
```

Update `backend/app.py` with the correct device path.

### 4.3 Test the backend manually

```bash
cd /home/user/openAnime
python3 backend/app.py
```

In a second terminal, open `http://localhost:8080` in Chromium and check the browser console for WebSocket connection confirmation. Press remote buttons — commands should appear.

### 4.4 Install the systemd service

```bash
sudo cp systemd/openanime.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openanime
sudo systemctl start openanime
```

Check it's running:
```bash
sudo systemctl status openanime
```

---

## Phase 5: Configure Autoboot to UI

### 5.1 Configure Openbox autostart

```bash
mkdir -p ~/.config/openbox
cat > ~/.config/openbox/autostart << 'EOF'
unclutter -idle 0 &
sleep 3
chromium --kiosk \
  --disable-extensions \
  --disable-plugins \
  --disable-translate \
  --disable-sync \
  --disable-background-networking \
  --disable-default-apps \
  --process-per-site \
  --no-first-run \
  http://localhost:8080 &
EOF
```

### 5.2 Configure LightDM autologin

```bash
sudo nano /etc/lightdm/lightdm.conf
```

Find (or create) the `[Seat:*]` section and set:
```ini
[Seat:*]
autologin-user=user
autologin-session=openbox
```

### 5.3 Disable screen blanking

Add to `~/.config/openbox/autostart` before the Chromium line:
```bash
xset s off
xset -dpms
xset s noblank
```

### 5.4 Reboot and test

```bash
sudo reboot
```

The TV should show the openAnime UI within ~30 seconds of powering on, with no keyboard input required.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Remote buttons do nothing | Wrong serial port in app.py | Run `dmesg \| grep tty` after pressing a button to find the port |
| Serial port permission denied | User not in dialout group | `sudo usermod -aG dialout user` then log out/in |
| Chromium won't open | Backend not ready yet | Increase `sleep 3` in Openbox autostart to `sleep 8` |
| No audio | Wrong ALSA card number | Re-run `aplay -l` and update `/etc/asound.conf` |
| Audio cuts out | Known Uno Q USB driver bug | Switch to Bluetooth speaker or USB hub with powered ports |
| yt-dlp fails | Site changed structure | Run `yt-dlp -U` to update, then retry |
| mpv won't go fullscreen over Chromium | wmctrl not installed | `sudo apt install wmctrl` |
| Black screen on boot | LightDM autologin misconfigured | SSH in and check `sudo systemctl status lightdm` |

---

## Keeping yt-dlp Updated

Streaming sites frequently update their players. Add a weekly cron job to keep yt-dlp current:

```bash
crontab -e
```

Add:
```
0 3 * * 1 /usr/local/bin/yt-dlp -U
```

This updates yt-dlp every Monday at 3am.
