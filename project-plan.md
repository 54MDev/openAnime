# Project Plan

## Directory Structure

```
openAnime/
├── firmware/
│   └── firmware.ino          # STM32 sketch: decodes IR, writes commands to serial
├── backend/
│   ├── app.py                # Main async loop: serial reader + WebSocket server + HTTP server
│   └── scraper.py            # yt-dlp wrapper: takes episode URL, returns stream URL
├── frontend/
│   ├── index.html            # UI layout
│   ├── style.css             # 10-foot styling, focus system
│   └── app.js                # WebSocket client, focus index, API calls
├── systemd/
│   └── openanime.service     # Systemd unit for auto-starting the backend
└── scripts/
    └── setup.sh              # One-shot provisioning script for fresh Debian install
```

---

## Step 1: IR Mapping Firmware

**File:** `firmware/firmware.ino`

**What it does:**
1. Listens on the KY-022 signal pin for IR pulses
2. IRremote.h decodes the pulse train into a protocol + hex code
3. On first run (detection mode): prints raw hex codes to serial so you can record your remote's codes
4. On final run (production mode): maps known hex codes to command strings and writes them to serial

**Key decisions:**
- Serial baud rate: `9600` (matches pyserial default; fast enough for IR commands)
- Output format: plain newline-terminated strings (`UP\n`, `DOWN\n`, etc.) — easy to `readline()` in Python
- Debounce: ignore repeated signals within 200ms to prevent key repeat flooding the backend

**Pins:**
- KY-022 Signal → STM32 GPIO (e.g. `PA0`, check Uno Q pinout for the MCU header)
- KY-022 VCC → 3.3V
- KY-022 GND → GND

---

## Step 2: Python Backend

**File:** `backend/app.py`

**What it does:**
Runs three concurrent tasks inside a single `asyncio` event loop:

```
┌─────────────────────────────────────────┐
│              app.py (asyncio)           │
│                                         │
│  [serial_reader]   reads /dev/ttyXXX   │
│        │                                │
│        └──> command_queue (asyncio.Queue)
│                    │                    │
│            [ws_broadcaster]  ──────────>│──> ws://localhost:8765
│                                         │
│  [http_server]  serves frontend/  ─────>│──> http://localhost:8080
└─────────────────────────────────────────┘
```

- `serial_reader`: blocking pyserial read wrapped in `asyncio.to_thread`; puts commands into a queue
- `ws_broadcaster`: reads from queue; broadcasts JSON `{"cmd": "UP"}` to all connected WebSocket clients
- `http_server`: serves the `frontend/` directory as static files

**Play endpoint:** `POST /play` with body `{"url": "https://..."}` calls `scraper.py`, gets stream URL, launches mpv as a subprocess, waits for it to exit.

---

## Step 3: Stream Scraper

**File:** `backend/scraper.py`

**What it does:**
Thin wrapper around yt-dlp. Takes a page URL (e.g. an animepahe episode page), runs yt-dlp to extract the direct stream URL, returns it to `app.py`.

```python
def get_stream_url(page_url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--get-url", "--no-playlist", page_url],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip().splitlines()[0]
```

**Quality selection:** Add `--format "bestvideo[height<=1080]+bestaudio/best[height<=1080]"` to cap at 1080p and avoid 4K streams that the QRB2210 may struggle to decode.

---

## Step 4: Frontend UI

**File:** `frontend/index.html` + `style.css` + `app.js`

### Layout (index.html)
```
┌──────────────────────────────────────────┐
│  HERO BANNER (featured anime, full width)│
├──────────────────────────────────────────┤
│  Row: Trending    [card][card][card]...  │
│  Row: New Episodes [card][card][card]... │
│  Row: Continue     [card][card][card]... │
└──────────────────────────────────────────┘
```

Each card is an `<article>` with `data-row` and `data-col` attributes. One card at a time holds the `.focused` class.

### Focus System (app.js)
- Maintain `focusRow` and `focusCol` integers
- On `LEFT`/`RIGHT`: increment/decrement `focusCol`, clamp to row length
- On `UP`/`DOWN`: increment/decrement `focusRow`, clamp `focusCol` to new row's length if needed
- On `OK`: read `data-url` from focused card, `POST /play`
- On `BACK`: if mpv is playing, send kill signal to backend; otherwise no-op

### WebSocket client (app.js)
```javascript
const ws = new WebSocket("ws://localhost:8765");
ws.onmessage = (e) => {
    const { cmd } = JSON.parse(e.data);
    handleCommand(cmd); // routes to focus system
};
```

Auto-reconnect with exponential backoff in case the backend restarts.

---

## Step 5: mpv Playback Integration

When `/play` is called:

1. Python calls `scraper.get_stream_url(url)` — takes ~3–8 seconds
2. Python broadcasts `{"cmd": "LOADING"}` to frontend (UI shows spinner)
3. Python minimizes Chromium: `subprocess.run(["wmctrl", "-r", "Chromium", "-b", "add,hidden"])`
4. Python launches mpv:
   ```
   mpv --fullscreen --ontop --audio-device=alsa/<device> \
       --format=... <stream_url>
   ```
5. Python `await`s mpv subprocess exit
6. Python un-hides Chromium: `subprocess.run(["wmctrl", "-r", "Chromium", "-b", "remove,hidden"])`
7. Python broadcasts `{"cmd": "PLAYBACK_ENDED"}` to frontend

The BACK button during playback sends `{"cmd": "STOP"}` via WebSocket → Python kills the mpv subprocess → step 6–7 run normally.

---

## Step 6: Systemd & Boot

**File:** `systemd/openanime.service`

```ini
[Unit]
Description=openAnime Backend
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/user/openAnime/backend/app.py
Restart=always
RestartSec=3
User=user

[Install]
WantedBy=multi-user.target
```

**Openbox autostart** (`~/.config/openbox/autostart`):
```bash
unclutter -idle 0 &
chromium-browser --kiosk --disable-extensions --disable-plugins \
  --disable-translate --disable-sync --disable-background-networking \
  --disable-default-apps --process-per-site --no-first-run \
  http://localhost:8080 &
```

**Autologin** (via `/etc/lightdm/lightdm.conf` or getty override):
```ini
[Seat:*]
autologin-user=user
autologin-session=openbox
```

---

## Data Flow Summary

```
[Remote Button Press]
        │
        ▼
[KY-022 IR Receiver]
        │  IR pulses
        ▼
[STM32 MCU - firmware.ino]
        │  "UP\n" over serial bridge
        ▼
[app.py - serial_reader]
        │  asyncio.Queue
        ▼
[app.py - ws_broadcaster]
        │  {"cmd":"UP"} over WebSocket
        ▼
[app.js - WebSocket client]
        │  handleCommand("UP")
        ▼
[Focus system updates .focused class]


[Remote OK press on a card]
        │
        ▼  (same IR path as above)
[app.js fires POST /play with episode URL]
        │
        ▼
[app.py /play handler]
        │  calls scraper.py
        ▼
[yt-dlp extracts stream URL]
        │
        ▼
[mpv launches fullscreen]
        │
        ▼
[User watches anime]
        │  (mpv exits or BACK pressed)
        ▼
[Chromium restored, UI resumes]
```
