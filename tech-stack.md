# Tech Stack

## Hardware

| Component | Part | Role |
|-----------|------|------|
| SBC | Arduino Uno Q (2GB RAM, 16GB eMMC) | Main compute; runs Debian Linux |
| MCU | STM32U585 (on-board) | Real-time GPIO; decodes IR signals |
| IR Receiver | KY-022 (TSOP1838, 38kHz) | Receives signals from TV remote |
| Remote | Universal NEC TV Remote | User input device |
| Hub | USB-C Multiport Adapter (HDMI + PD + USB-A) | Video out, power in, USB peripherals |
| Audio | USB-C Audio Adapter (into hub USB-A port) | Audio out (HDMI audio unsupported on QRB2210) |
| Display | Any HDMI TV/Monitor | Output |

---

## Firmware (STM32 MCU)

| Technology | Version | Purpose |
|------------|---------|---------|
| Arduino C++ | — | MCU firmware language |
| IRremote.h | 4.x | Decodes IR signals from KY-022; outputs named command strings over serial bridge |

The STM32 acts purely as an IR translator. It receives raw IR pulses, decodes them into hex codes, maps them to human-readable strings (`UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`, `BACK`), and writes them to the internal serial bridge shared with the Linux MPU side.

---

## Backend (Debian Linux / Python)

| Technology | Version | Purpose |
|------------|---------|---------|
| Python | 3.11+ | Backend runtime |
| pyserial | 3.5+ | Reads command strings from the STM32 over internal serial bridge |
| websockets | 12.x | Broadcasts IR commands to the frontend in real time |
| yt-dlp | latest | Extracts direct stream URLs from anime sites; replaces custom scraper |
| asyncio | stdlib | Runs serial listener and WebSocket server concurrently on one thread |
| subprocess | stdlib | Launches and monitors mpv for video playback |

### Why Python over Node.js
The Uno Q's App Lab SDK has first-class Python support for the MCU↔MPU serial bridge. Python's asyncio handles the serial + WebSocket concurrency cleanly without threads.

### Why yt-dlp over BeautifulSoup
Target sites (animepahe, anikoto, reanime) are JavaScript-rendered SPAs. BeautifulSoup only parses static HTML and cannot execute JS or bypass Cloudflare. yt-dlp handles all of this internally and is actively maintained against site changes.

---

## Frontend (Browser UI)

| Technology | Purpose |
|------------|---------|
| HTML5 | Layout structure |
| CSS Grid + Flexbox | 10-foot media wall layout; card rows |
| CSS Custom Properties | Theme tokens (colors, focus ring, transitions) |
| Vanilla JavaScript (ES2022) | WebSocket listener, focus index management, API calls |

No framework. The UI is simple enough that React/Vue would add RAM overhead with no benefit on a 2GB device.

---

## Display Stack (Linux)

| Technology | Purpose |
|------------|---------|
| X11 | Display server |
| Openbox | Bare-bones window manager; no desktop environment |
| Chromium (kiosk mode) | Renders the frontend UI fullscreen, no chrome/borders |
| mpv | Hardware-accelerated borderless video player; launched over UI during playback |

### Chromium launch flags (memory optimization)
```
--kiosk
--disable-extensions
--disable-plugins
--disable-translate
--disable-sync
--disable-background-networking
--disable-default-apps
--process-per-site
--no-first-run
```

### Why Openbox over GNOME/XFCE
A full desktop environment consumes 300–500MB of RAM before any app launches. Openbox uses ~5MB, leaving the full 2GB budget for Chromium, Python, and mpv.

### Why mpv over VLC or browser-based player
mpv uses the QRB2210's hardware video decode acceleration (H.264/H.265), supports raw HLS (.m3u8) and direct MP4 streams natively, and can be spawned fully borderless over any window. VLC is heavier; browser-based players cannot hardware-decode at this level.

---

## Audio

HDMI audio is non-functional on the QRB2210 at the driver level (open issue: [arduino/linux-qcom#1](https://github.com/arduino/linux-qcom/issues/1)). Audio is routed through a USB audio adapter recognized as an ALSA device. mpv is configured to target this device explicitly.

---

## Networking

| Protocol | Use |
|----------|-----|
| Wi-Fi 5 (built-in) | Internet connectivity for streaming |
| WebSocket (ws://localhost:8765) | Internal IPC between Python backend and browser UI |
| HTTP (http://localhost:8080) | Python serves the frontend static files |
