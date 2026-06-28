# Roadmap

## Overview

Five milestones, each independently testable before moving to the next. Never move to the next milestone until the current one is verified working.

---

## Milestone 1 — IR Input Pipeline
**Goal:** Press a button on the remote → see the correct string printed in a terminal on the Linux side.

- [ ] Wire KY-022 to STM32 GPIO
- [ ] Upload IR detection sketch; verify raw hex codes print over serial
- [ ] Map D-pad + OK + Back hex codes; hardcode into firmware
- [ ] Upload final firmware; verify `UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`, `BACK` print on Linux serial monitor

**Done when:** `python3 -c "import serial; s=serial.Serial('/dev/ttyXXX',9600); print(s.readline())"` prints the correct command string every time a button is pressed.

---

## Milestone 2 — Backend Bridge ✅ COMPLETE (verified on hardware)
**Goal:** IR command from remote → WebSocket message received in a browser tab.

- [x] Write `app.py` with asyncio arduino-router socket reader (MessagePack-RPC) + WebSocket server on port 8765
- [x] Write a minimal test HTML page that connects to ws://localhost:8765 and logs messages
- [x] Open test page in Chromium; verify button presses appear in browser console

**Done when:** Every remote button press produces a logged WebSocket message in the browser with zero dropped inputs.

---

## Milestone 3 — UI Shell
**Goal:** A fullscreen, mouse-free anime browse UI that responds to D-pad navigation.

- [x] Build `frontend/index.html` layout: hero banner + card rows
- [x] Write `frontend/style.css`: 10-foot sizing, focus ring animation, card hover scale
- [x] Write `frontend/app.js`: WebSocket listener, 2D focus index grid, keyboard fallback
- [x] Test navigation across all card rows with the remote
- [x] Wire card selection (`OK`) to fire a placeholder `POST /play` request to the backend

**Done when:** All cards are navigable via remote with smooth focus transitions; selecting a card triggers the backend endpoint.

---

## Milestone 4 — Stream Extraction & Playback
**Goal:** Select an anime episode → video plays fullscreen via mpv with audio.

- [x] Write `backend/scraper.py` (yt-dlp wrapper: anikoto search → watch page → `.m3u8` + referer/UA headers)
- [x] Test yt-dlp against the source — animepahe & AllAnime are Cloudflare-challenge-blocked; **anikoto.cz works** headlessly via a vendored, patched `yt-dlp-anikoto` plugin (`backend/plugins/`). Verified on dev machine: search match, m3u8 extraction, playlist + real MPEG-TS segments fetch with referer.
- [x] Wire `/play` to call scraper and launch `mpv --fullscreen --ontop` (forwarding `--referrer`/`--user-agent`); add `/stop` for BACK during playback
- [x] Verify Chromium suspends cleanly while mpv plays (on hardware)
- [x] Verify mpv exit returns focus to the browser UI (on hardware)
- [x] Confirm audio plays through USB audio adapter (ALSA device) (on hardware)

**Done when:** End-to-end flow works: browse → select episode → video plays with audio → exit returns to UI.

**Note:** Extraction + the full HTTP/play/stop wiring are verified on the dev
machine (real network, fake mpv). The remaining boxes need the actual Uno Q
(mpv playback, Chromium hide/restore via wmctrl, USB audio).

---

## Milestone 5 — Appliance Polish
**Goal:** The device boots directly into the UI with no manual intervention; feels like a consumer product.

- [x] Write systemd service for `app.py` (auto-start, auto-restart on crash) — `systemd/openanime.service`
- [x] Write autostart entry for Openbox to launch Chromium kiosk on boot — `appliance/openbox-autostart`
- [x] Configure Debian to boot to X11 + Openbox without login prompt (autologin) — via `scripts/install-appliance.sh` (LightDM `conf.d` drop-in)
- [x] Hide cursor (`unclutter` package) — `unclutter -idle 0` in autostart
- [x] Test cold boot → UI ready time; target under 30 seconds — verified on hardware
- [x] Disable screen blanking / DPMS — `xset s off / -dpms / s noblank` in autostart
- [x] Add BACK button handler to close mpv and return to UI — `frontend/app.js` posts `/stop` on BACK during playback

**Done when:** Power on the Uno Q → TV shows the UI within 30 seconds, no keyboard or mouse ever touched.

**✅ COMPLETE (verified on hardware 2026-06-27).** Appliance files self-install
via `sudo bash scripts/install-appliance.sh` (idempotent); cold boot lands on the
UI with no keyboard. All five milestones done.

---

## Stretch Goals (Post-MVP)

| Goal | Notes |
|------|-------|
| Search functionality | Add a search bar triggered by a long-press of OK; requires an on-screen keyboard or phone-based text input |
| Continue watching | Track episode progress in a local SQLite file |
| Multiple sources | Add fallback: if yt-dlp fails on one site, retry on the next |
| Watchlist | Persist a favorites list in a local JSON file |
| OTA updates | `git pull && systemctl restart openanime` triggered from a remote button combo |
