# Build Instructions

## Prerequisites

Before starting, you need:
- Arduino Uno Q with Debian Linux booted and SSH or keyboard/monitor access
- KY-022 IR receiver wired to the MCU header (Signal → A0, VCC → 3.3V or 5V, GND → GND)
- USB-C audio adapter plugged into the hub's USB-A port
- USB-C hub connected (HDMI to TV, PD in for power)
- Internet connection (Wi-Fi configured or Ethernet dongle)
- This repo cloned to `/home/user/openAnime/` on the Uno Q

---

## Phase 1: Flash the Firmware

### 1.1 Install Arduino IDE on your dev machine (not the Uno Q)

Download from [arduino.cc/en/software](https://www.arduino.cc/en/software). The Uno Q's STM32 is programmed from a host computer via USB, not from the Linux side directly.

### 1.2 Add the Arduino UNO Q board package

In Arduino IDE → Tools → Board → Board Manager → search "UNO Q" → install the
**Arduino UNO Q** package (provides the `arduino:zephyr` core). The Uno Q's MCU
runs on Zephyr, **not** STM32duino — do not install the STM32 package.

Then select **Tools → Board → Arduino UNO Q** and the port that appears as
`/dev/cu.usbmodem...` (the other ports — wlan-debug, debug-console, Bluetooth —
are the Linux side; ignore them).

### 1.3 Do NOT use the IRremote library

The IRremote library and Arduino's `pulseIn()` both fail on the Zephyr core
(IRremote errors with "no timer functions implemented for this CPU / board";
`pulseIn()` silently returns 0). The firmware in this repo decodes NEC manually
by timing raw GPIO edges with `digitalRead()` + `micros()`, which works on Zephyr.
No external library is required.

### 1.4 Run the detection sketch first

Before uploading the final firmware, upload `firmware/ir_detect/ir_detect.ino`
to find your remote's hex codes. It captures raw NEC pulses and prints a hex
code per button press.

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

The final firmware sends each button to the Linux side over the **Arduino Router
Bridge** (`Bridge.notify("ir_command", "UP")`), not over plain `Serial` — that's
the only way to reach the Linux MPU on the Uno Q. Install the **Arduino_RouterBridge**
library first (Arduino IDE → Library Manager → search "Arduino_RouterBridge").

Open `firmware/firmware/firmware.ino`, replace the hex values near the top with
your recorded codes, make sure the board is **Arduino UNO Q** and the correct
port is selected, and upload.

**Verify:** the firmware echoes each press to the App Lab Monitor via
`Monitor.println()`. The real end-to-end check happens in Phase 4 once the
backend is running — pressing a button should reach the browser over WebSocket.

**Wiring note:** The KY-022 pin order is not obvious — confirm Signal (`S`),
VCC (middle), and GND (`−`) before powering up. Swapping VCC/Signal lets the
module's LED flash but produces no usable output. The signal wire must go to
**A0** (an analog-labeled pin used here as a digital input).

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

On the Uno Q the MCU does **not** expose a plain serial port to Linux — the
`arduino-router` daemon owns that link and speaks MessagePack-RPC over a Unix
socket. So the backend needs `msgpack` and `websockets`, not pyserial. Install
them from apt (newer Debian blocks system-wide `pip install`):

```bash
sudo apt install -y python3-msgpack python3-websockets
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

### 4.2 The router socket (usually no config needed)

The MCU talks to Linux through the `arduino-router` daemon, which listens on a
Unix socket at `/var/run/arduino-router.sock`. `backend/app.py` connects there by
default — there is normally nothing to configure. Confirm the socket exists:
```bash
ls -l /var/run/arduino-router.sock
```
If your image puts it elsewhere, override with `--router /path/to.sock` or the
`OPENANIME_ROUTER` env var.

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
| Remote buttons do nothing | Backend not registered with router, or firmware not using Bridge | Check `app.py` log shows `registered 'ir_command'`; confirm firmware uses `Bridge.notify` and Arduino_RouterBridge is installed |
| `cannot connect to ...arduino-router.sock` | Router daemon down or socket moved | `systemctl status arduino-router`; check `ls /var/run/arduino-router.sock`, override with `--router` |
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
