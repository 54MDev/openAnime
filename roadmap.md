# Roadmap

## Overview

Six milestones, each independently testable before moving to the next. Never move to the next milestone until the current one is verified working. (M1–M5 are the original MVP; M6 is a post-MVP expansion adding in-player playback controls.)

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

## Milestone 6 — In-Player Playback Controls ✅ COMPLETE (verified on hardware)
**Goal:** While an episode is playing, the remote can pause/resume, seek ±10 seconds, and a progress bar shows position — all on-screen, no keyboard.

### Scope (decided)
- **OK** → pause / play toggle.
- **LEFT** → seek −10 s, **RIGHT** → seek +10 s.
- **BACK** → stop (unchanged from M4/M5).
- **UP / DOWN** → intentionally unused for now (reserved; possible future volume control). Out of scope: skip-intro, next/previous-episode.
- **Progress bar** → mpv's built-in OSD bar, flashed on pause/seek, auto-hides after a couple seconds. No custom UI.

### Why mpv owns the controls (read before implementing)
During playback Chromium is **hidden** (`wmctrl -r Chromium -b add,hidden` in [backend/app.py](backend/app.py) `play_blocking`), so the frontend cannot draw over the video. The controls and progress bar therefore come from **mpv itself**, driven over mpv's JSON IPC socket. The remote → backend → mpv path is:
`IR command → app.py broadcast → WebSocket → app.js (playing screen) → POST to backend → backend writes JSON to mpv's IPC socket.`
This mirrors how BACK→`/stop` already works today; pause/seek are the same pattern with new endpoints.

### Implementation notes for the next session
- **Launch flag:** add `--input-ipc-server=<socket>` to the mpv command in `play_blocking`. Put the socket under the runtime dir, e.g. `/run/user/<uid>/openanime-mpv.sock` (or `/tmp/openanime-mpv.sock`). Store the path so the control handlers can reach it; mpv creates the socket on launch, so it exists by the time any control request arrives (the long-lived `/play` request is already running by then).
- **Backend IPC helper:** small function that opens the IPC socket and writes one newline-terminated JSON command. Guard it the same way `stop_playback()` is — no-op (return "idle") when `_mpv_proc` is None / not running. IPC writes are independent of `_play_lock` (they don't touch `_mpv_proc` lifecycle), but read `_mpv_proc` under the lock to check liveness.
  - Pause toggle: `{"command":["cycle","pause"]}`
  - Seek: `{"command":["seek", 10]}` / `{"command":["seek", -10]}` (relative)
  - Flash the bar after each action: `{"command":["show-progress"]}` (mpv's OSD bar also auto-shows on seek; `show-progress` covers the pause case and gives consistent feedback).
- **New HTTP endpoints** in `FrontendHandler.do_POST` alongside `/play` and `/stop`:
  - `POST /pause` → toggle, return `{"status":"toggled"|"idle"}`
  - `POST /seek` with body `{"delta": 10}` (or `-10`) → return `{"status":"seeking"|"idle"}`
  - Keep them tiny and non-blocking (unlike `/play`, these return immediately).
- **Frontend** [frontend/app.js](frontend/app.js): the playing-screen branch currently handles only `BACK → stopPlayback()` (it's handled first so overlay-dismiss can't swallow it). Add in that same branch: `OK → POST /pause`, `LEFT → POST /seek {-10}`, `RIGHT → POST /seek {+10}`. Reuse the existing `STOP_URL` host pattern for the new URLs. No new screen state needed — still `screen === "playing"`.
- **Env knobs:** follow the existing convention — e.g. `OPENANIME_MPV_IPC` for the socket path, optionally `OPENANIME_SEEK_STEP` (default 10) so the seek amount is tunable without code changes.
- **Don't break:** the long-lived blocking `/play` request and its `wmctrl` hide/restore must be untouched. Pause via IPC does not affect `proc.wait()`. The `_mpv_proc`/`_play_lock` invariants stay as-is.

### Tasks
- [x] Add `--input-ipc-server` to the mpv launch; store/derive the socket path — `OPENANIME_MPV_IPC` (default `/tmp/openanime-mpv.sock`)
- [x] Backend IPC helper (open socket, send JSON, no-op when idle) — `_mpv_ipc()` + `toggle_pause()` / `seek_relative()` in [backend/app.py](backend/app.py)
- [x] `POST /pause` and `POST /seek` endpoints
- [x] Map OK/LEFT/RIGHT in app.js's playing-screen input branch
- [x] Dev-machine test with real mpv (a local file): verify pause, seek both directions, progress bar appears and auto-hides — pause toggles, seek exactly ±10 s on a seekable file, idle no-ops return `idle`
- [x] On-hardware test via the remote: pause/resume, ±10 s seek, BACK still stops cleanly

**Done when:** During an episode, the remote pauses/resumes with on-screen feedback, LEFT/RIGHT seek ±10 s with the progress bar flashing the new position, the bar auto-hides, and BACK still exits to the UI — all without a keyboard.

**✅ COMPLETE (verified on hardware 2026-06-28).** OK pauses/resumes, LEFT/RIGHT
seek ±10 s with mpv's OSD bar flashing the new position, and BACK still exits
cleanly — all from the remote, no keyboard. All six milestones done.

---

## Milestone 7A — Episode Progress Tracking
**Goal:** Every episode play records how far you got; the detail screen shows a
progress bar under each episode tile, and replaying resumes where you stopped.

Backend polls mpv's IPC socket for position during the long-lived `/play`
session, persists per-episode progress (keyed by AniList id + episode) to a JSON
store, and exposes `GET /progress`. Frontend draws a bar on each episode tile.

**Full spec:** [milestone-7a.md](milestone-7a.md).

**Status:** Built + dev-verified (real mpv on local clips: partial-watch
persistence, completion threshold, and resume floor/mid/completed cases all
pass). On-hardware remote test pending.

---

## Milestone 7B — Continue Watching Row
**Goal:** The home screen shows a "Continue Watching" row first, listing series
with an unfinished episode (most-recent first); selecting one opens its normal
episode list — no re-searching.

Builds on 7A: reads the progress store (which already stores a media snapshot
per record), groups by series in the frontend, and prepends a row to the home
catalog. Almost entirely frontend.

**Full spec:** [milestone-7b.md](milestone-7b.md).

**Status:** Built; grouping/filter logic dev-verified against the progress store.
On-hardware remote test (browse → continue card → episode list) pending.

---

## Stretch Goals (Post-MVP)

| Goal | Notes |
|------|-------|
| Search functionality | Add a search bar triggered by a long-press of OK; requires an on-screen keyboard or phone-based text input |
| Continue watching | Track episode progress in a local SQLite file |
| Multiple sources | Add fallback: if yt-dlp fails on one site, retry on the next |
| Watchlist | Persist a favorites list in a local JSON file |
| OTA updates | `git pull && systemctl restart openanime` triggered from a remote button combo |
