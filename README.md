# openAnime

A boot-to-UI anime streaming appliance built on the [Arduino Uno Q](https://www.arduino.cc/), controlled entirely by a TV remote over IR. Point a universal remote at a KY-022 IR receiver, and the Uno Q's on-board STM32 MCU decodes button presses and forwards them to a Python backend on the Linux side, which drives a 10-foot browser UI and launches `mpv` for hardware-accelerated playback.

No keyboard, no mouse — power on the device and it lands directly on the anime browse screen.

---

## How it works

```
TV Remote --IR--> KY-022 --GPIO--> STM32 MCU (Zephyr firmware)
                                        |
                                  Bridge.notify("ir_command", ...)
                                        |
                               arduino-router (MessagePack-RPC, Unix socket)
                                        |
                              backend/app.py (asyncio)
                                   /         \
                          WebSocket :8765   HTTP :8080
                                   |              |
                          frontend/app.js   frontend/index.html
                                   |
                          POST /play -> yt-dlp (anikoto.cz) -> mpv
```

See [tech-stack.md](tech-stack.md) for the full hardware/software breakdown and rationale, and [roadmap.md](roadmap.md) for the milestone-by-milestone build history.

---

## Gallery

images/IMG_8354.jpeg
images/IMG_8391.jpeg
images/IMG_8393.jpeg
images/IMG_8395.jpeg

---

## My environment

| Component | Details |
|-----------|---------|
| SBC | Arduino Uno Q — 2GB RAM, 16GB eMMC, Debian Linux on a Qualcomm QRB2210 |
| MCU | On-board STM32U585, runs Zephyr (not STM32duino) |
| IR receiver | KY-022 (TSOP1838, 38kHz), signal wired to MCU pin `A0` |
| Remote | Universal NEC-protocol TV remote |
| Video out | USB-C multiport hub → HDMI |
| Audio out | USB-C audio adapter into the hub's USB-A port (HDMI audio is unsupported on this SoC) |
| Display server | X11 + Openbox (no full desktop environment) |
| Browser | Chromium in kiosk mode |
| Player | mpv (hardware-accelerated H.264/H.265, native HLS) |
| Backend | Python 3.11+, `msgpack`, `websockets`, `yt-dlp` |
| Stream source | anikoto.cz via a vendored/patched `yt-dlp-anikoto` plugin |

Full rationale for each choice (why Python, why no framework on the frontend, why Openbox over a full DE, why anikoto over animepahe/AllAnime) is in [tech-stack.md](tech-stack.md).

---

## Setup

Full step-by-step instructions — flashing the firmware, installing Linux dependencies, wiring audio, and configuring boot-to-UI autostart — are in **[build-instructions.md](build-instructions.md)**.

Quick path once the hardware is wired and firmware flashed:

```bash
git clone https://github.com/54MDev/openAnime.git /home/user/openAnime
cd /home/user/openAnime

# Linux dependencies (see build-instructions.md for the full list)
sudo apt install -y python3 python3-pip chromium openbox x11-xserver-utils xorg mpv wmctrl unclutter lightdm
sudo apt install -y python3-msgpack python3-websockets

# yt-dlp (>= 2025.12.08)
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

# Test the backend manually
python3 backend/app.py

# Once verified, install as a boot-to-UI appliance
sudo bash scripts/install-appliance.sh
sudo reboot
```

Run without hardware attached using `python3 backend/app.py --mock` to type IR commands by hand.

---

## Repository layout

```
firmware/           Arduino Zephyr sketches (IR detection + final NEC decoder/bridge)
backend/            Python asyncio bridge: router socket -> WebSocket, HTTP static server, mpv control, scraper
frontend/           Vanilla HTML/CSS/JS 10-foot UI (no framework)
appliance/          Openbox autostart script for kiosk boot
systemd/            openanime.service unit
scripts/            install-appliance.sh — idempotent appliance installer
tech-stack.md       Hardware/software choices and why
build-instructions.md   Full setup walkthrough
roadmap.md          Milestone-by-milestone build plan and status
```

---

## Known issues

- HDMI audio does not work on this SoC ([arduino/linux-qcom#1](https://github.com/arduino/linux-qcom/issues/1)) — audio must go through a USB or Bluetooth adapter.
- USB audio can drop out after a few minutes on some units; Bluetooth audio is a more stable workaround.
- Streaming extraction depends on `yt-dlp` and a vendored patched plugin for anikoto.cz; both need periodic updates as the site changes (see `backend/plugins/yt-dlp-anikoto/PATCHES.md`).

See the troubleshooting table in [build-instructions.md](build-instructions.md) for more.

## Future Development
I'm CADing a box/container for this project and will be pushed soon. you'll see it later.