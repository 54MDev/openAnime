#!/usr/bin/env python3
"""openAnime backend bridge.

Receives named IR commands from the STM32 MCU and broadcasts each one to every
connected browser client over a WebSocket.

On the Uno Q the MCU does NOT expose a plain serial port to Linux -- the
arduino-router daemon owns that link (/dev/ttyHS1) and multiplexes it over a
Unix socket using MessagePack-RPC. The firmware sends each button with
    Bridge.notify("ir_command", "UP")
so this script connects to the router's Unix socket, registers the method
"ir_command", and rebroadcasts whatever it receives.

Run:
    python3 backend/app.py                 # read from the arduino-router
    python3 backend/app.py --mock          # type commands by hand (no hardware)

Override the socket path with --router or the OPENANIME_ROUTER env var.
"""

import argparse
import asyncio
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import websockets

import scraper

try:
    import msgpack
except ImportError:
    msgpack = None

WS_HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_HOST = "0.0.0.0"
HTTP_PORT = int(os.environ.get("OPENANIME_HTTP_PORT", "8080"))
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ROUTER_SOCKET = os.environ.get("OPENANIME_ROUTER", "/var/run/arduino-router.sock")
IR_METHOD = "ir_command"
VALID_COMMANDS = {"UP", "DOWN", "LEFT", "RIGHT", "OK", "BACK"}

# Playback config (overridable via env on the device).
MPV_BIN = os.environ.get("OPENANIME_MPV", "mpv")
# e.g. "alsa/default" or a specific card "alsa/plughw:1,0". Unset = mpv default,
# which honours /etc/asound.conf (see build-instructions.md, Phase 3).
AUDIO_DEVICE = os.environ.get("OPENANIME_AUDIO_DEVICE")
# wmctrl matches window title substrings; Chromium's title ends in "Chromium".
BROWSER_WINDOW = os.environ.get("OPENANIME_BROWSER", "Chromium")
# mpv's JSON IPC socket: the control handlers (/pause, /seek) write commands here
# while playback is live. mpv creates it on launch via --input-ipc-server.
MPV_IPC_SOCKET = os.environ.get("OPENANIME_MPV_IPC", "/tmp/openanime-mpv.sock")
SEEK_STEP = int(os.environ.get("OPENANIME_SEEK_STEP", "10"))

# Progress tracking (M7A). Persisted outside the repo so `git pull` OTA stays
# clean. Keyed by "<anilistId>:<episode>".
DATA_DIR = Path(os.environ.get("OPENANIME_DATA_DIR",
                               Path.home() / ".local/share/openanime"))
PROGRESS_FILE = DATA_DIR / "progress.json"
POLL_SECS = float(os.environ.get("OPENANIME_POLL_SECS", "5"))
COMPLETE_PCT = float(os.environ.get("OPENANIME_COMPLETE_PCT", "0.90"))
# Don't auto-resume from a trivial start, and don't resume right at the end.
RESUME_FLOOR_SECS = 15.0
RESUME_TAIL_SECS = 30.0

# MessagePack-RPC message type codes
REQUEST, RESPONSE, NOTIFICATION = 0, 1, 2

clients = set()


async def register(websocket):
    """Track a connected browser client for the lifetime of its connection."""
    clients.add(websocket)
    print(f"[ws] client connected ({len(clients)} total)")
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)
        print(f"[ws] client disconnected ({len(clients)} total)")


def broadcast(command):
    """Send one validated command to all connected browser clients."""
    if command not in VALID_COMMANDS:
        print(f"[ir] ignored unrecognized command: {command!r}")
        return
    if not clients:
        print(f"[ir] {command} (no clients connected)")
        return
    print(f"[ir] {command} -> {len(clients)} client(s)")
    websockets.broadcast(clients, command)


# =====================================================================
# Progress store (M7A): persist how far the viewer got in each episode, keyed
# by "<anilistId>:<episode>". Read in full on each request; written atomically
# under a lock so a crash mid-write can't corrupt the file.
# =====================================================================

_progress_lock = threading.Lock()


def _progress_key(anilist_id, episode):
    return f"{anilist_id}:{episode}"


def load_progress():
    """Return the whole progress map (empty dict if the file is missing/bad)."""
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except OSError as e:
        print(f"[progress] read failed: {e}", file=sys.stderr)
        return {}


def save_progress_record(record):
    """Merge one episode record into the store (atomic write under the lock)."""
    key = _progress_key(record["anilistId"], record["episode"])
    with _progress_lock:
        store = load_progress()
        store[key] = record
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = PROGRESS_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(store, f)
            os.replace(tmp, PROGRESS_FILE)
        except OSError as e:
            print(f"[progress] write failed: {e}", file=sys.stderr)


# =====================================================================
# Playback: resolve a stream, launch mpv over the browser, restore on exit.
#
# Each /play request runs on its own ThreadingHTTPServer thread and blocks for
# the whole viewing session: the HTTP response doesn't return until mpv exits
# (naturally or via /stop). The frontend treats that long-lived request as the
# "now playing" state, so there's no separate event channel to keep in sync.
# =====================================================================

_play_lock = threading.Lock()
_mpv_proc = None  # the live mpv process, or None when nothing is playing


def _wmctrl(state):
    """Toggle the browser window's hidden state (no-op if wmctrl is missing)."""
    try:
        subprocess.run(["wmctrl", "-r", BROWSER_WINDOW, "-b", state],
                       timeout=5, capture_output=True)
    except FileNotFoundError:
        pass  # dev machine without wmctrl / not in kiosk mode
    except Exception as e:
        print(f"[play] wmctrl {state} failed: {e}", file=sys.stderr)


def _resume_position(anilist_id, episode):
    """Saved position to resume from, or None to start at 0 (M7A).

    Skips trivial starts (< floor) and near-finished episodes (within the tail
    or already marked completed) so replay starts clean in those cases.
    """
    if anilist_id is None:
        return None
    record = load_progress().get(_progress_key(anilist_id, episode))
    if not record or record.get("completed"):
        return None
    pos = record.get("position") or 0
    dur = record.get("duration") or 0
    if pos < RESUME_FLOOR_SECS:
        return None
    if dur and pos > dur - RESUME_TAIL_SECS:
        return None
    return pos


def _poll_progress(proc, anilist_id, episode, title, audio, media):
    """Poll mpv's position while it plays; persist the final snapshot on exit.

    Runs on its own daemon thread. Keeps the latest (position, duration) and
    writes one record after mpv exits — covering BACK/stop, remote-quit, and
    natural end-of-file uniformly. No-op when we have nothing to key on.
    """
    if anilist_id is None:
        return
    last_pos = 0.0
    last_dur = 0.0
    while proc.poll() is None:
        pos = _mpv_get("time-pos")
        dur = _mpv_get("duration")
        if isinstance(pos, (int, float)):
            last_pos = float(pos)
        if isinstance(dur, (int, float)) and dur > 0:
            last_dur = float(dur)
        time.sleep(POLL_SECS)

    if last_pos <= 0:
        return  # never got a reading (e.g. mpv failed to start playback)
    percent = (last_pos / last_dur) if last_dur else 0.0
    save_progress_record({
        "anilistId": anilist_id,
        "episode": episode,
        "position": round(last_pos, 1),
        "duration": round(last_dur, 1),
        "percent": round(percent, 4),
        "completed": percent >= COMPLETE_PCT,
        "updatedAt": int(time.time()),
        "title": title,
        "audio": audio,
        "media": media,
    })
    print(f"[progress] saved {title} ep {episode}: "
          f"{last_pos:.0f}/{last_dur:.0f}s ({percent:.0%})")


def play_blocking(stream, title, episode, anilist_id=None, media=None, audio="sub"):
    """Launch mpv and block until it exits. Returns a status dict for the UI.

    `stream` is a scraper.Stream (url + http_headers). The extracted .m3u8 is
    referer-gated, so Referer/User-Agent from the scraper must be forwarded or
    the CDN 403s.
    """
    global _mpv_proc
    cmd = [MPV_BIN, "--fullscreen", "--ontop", "--no-terminal", "--really-quiet",
           f"--input-ipc-server={MPV_IPC_SOCKET}",
           f"--force-media-title={title} — Episode {episode}"]
    # Resume where the viewer left off (M7A), if there's saved unfinished progress.
    resume = _resume_position(anilist_id, episode)
    if resume:
        cmd.append(f"--start={resume:.1f}")
        print(f"[play] resuming {title} ep {episode} at {resume:.0f}s")
    if AUDIO_DEVICE:
        cmd.append(f"--audio-device={AUDIO_DEVICE}")
    # Forward the gating headers. (Only referer + UA; other headers like Accept
    # contain commas, which mpv's --http-header-fields would mis-split.)
    for key, value in (stream.headers or {}).items():
        if key.lower() == "referer":
            cmd.append(f"--referrer={value}")
        elif key.lower() == "user-agent":
            cmd.append(f"--user-agent={value}")
    # External English subtitle for sub playback (loads + shows automatically).
    # The .vtt is referer-gated too, but mpv applies --referrer to all requests.
    if stream.subtitle:
        cmd.append(f"--sub-file={stream.subtitle}")
    cmd.append(stream.url)

    with _play_lock:
        if _mpv_proc and _mpv_proc.poll() is None:
            return {"status": "busy"}  # already watching something
        _wmctrl("add,hidden")  # suspend Chromium so mpv owns the screen
        try:
            _mpv_proc = subprocess.Popen(cmd)
        except FileNotFoundError:
            _wmctrl("remove,hidden")
            return {"status": "error", "error": f"{MPV_BIN} not found"}
        proc = _mpv_proc

    print(f"[play] mpv started: {title} ep {episode}")
    # Track position over the IPC socket and persist it when mpv exits (M7A).
    poller = threading.Thread(
        target=_poll_progress,
        args=(proc, anilist_id, episode, title, audio, media),
        daemon=True)
    poller.start()
    rc = proc.wait()  # blocks here for the whole session (lock released)
    poller.join(timeout=POLL_SECS + 3)  # let it write the final snapshot

    with _play_lock:
        if _mpv_proc is proc:
            _mpv_proc = None
        _wmctrl("remove,hidden")  # restore Chromium / the UI
    print(f"[play] mpv exited ({rc}); browser restored")
    return {"status": "ended", "code": rc}


def stop_playback():
    """Ask the current mpv to quit; play_blocking() handles the restore."""
    with _play_lock:
        if _mpv_proc and _mpv_proc.poll() is None:
            print("[play] STOP -> terminating mpv")
            _mpv_proc.terminate()
            return True
    return False


def _mpv_ipc(command):
    """Write one newline-terminated JSON command to mpv's IPC socket.

    Returns True if it was sent. No-op (False) when nothing is playing. The
    write is independent of the mpv lifecycle (_play_lock), but liveness is
    checked under the lock to avoid racing with play/stop.
    """
    with _play_lock:
        live = _mpv_proc is not None and _mpv_proc.poll() is None
    if not live:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(MPV_IPC_SOCKET)
            sock.sendall((json.dumps({"command": command}) + "\n").encode())
        return True
    except OSError as e:
        print(f"[play] mpv IPC failed: {e}", file=sys.stderr)
        return False


_ipc_request_id = 0


def _mpv_get(prop):
    """Read one mpv property over IPC (get_property). Returns the value or None.

    Unlike _mpv_ipc (write-only), this waits for the matching response, skipping
    the unsolicited `event` messages mpv interleaves on the same socket. Returns
    None when idle, on error, or when the property is unavailable (e.g. time-pos
    before the first frame decodes).
    """
    global _ipc_request_id
    with _play_lock:
        live = _mpv_proc is not None and _mpv_proc.poll() is None
    if not live:
        return None
    _ipc_request_id += 1
    req_id = _ipc_request_id
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(MPV_IPC_SOCKET)
            cmd = {"command": ["get_property", prop], "request_id": req_id}
            sock.sendall((json.dumps(cmd) + "\n").encode())
            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    return None
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("request_id") == req_id:
                        if msg.get("error") == "success":
                            return msg.get("data")
                        return None
    except OSError as e:
        print(f"[play] mpv get {prop} failed: {e}", file=sys.stderr)
        return None


def toggle_pause():
    """Flip pause/play and flash mpv's OSD progress bar."""
    if not _mpv_ipc(["cycle", "pause"]):
        return False
    _mpv_ipc(["show-progress"])
    return True


def seek_relative(delta):
    """Seek `delta` seconds (signed, relative) and flash the progress bar."""
    if not _mpv_ipc(["seek", delta]):
        return False
    _mpv_ipc(["show-progress"])
    return True


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the frontend/ directory and handles the placeholder /play POST.

    Static files (index.html, style.css, app.js) are served straight from
    FRONTEND_DIR. POST /play resolves a stream and blocks for the viewing
    session; POST /stop ends the current playback.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self):
        # M7A: serve the progress map as JSON; everything else is a static file.
        if self.path.rstrip("/") == "/progress":
            self._json(200, load_progress())
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/play":
            self._handle_play()
        elif path == "/stop":
            stopped = stop_playback()
            self._json(200, {"status": "stopping" if stopped else "idle"})
        elif path == "/pause":
            toggled = toggle_pause()
            self._json(200, {"status": "toggled" if toggled else "idle"})
        elif path == "/seek":
            self._handle_seek()
        else:
            self._json(404, {"error": "not found"})

    def _handle_seek(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"status": "error", "error": "invalid JSON"})
            return
        try:
            delta = float(payload.get("delta", SEEK_STEP))
        except (TypeError, ValueError):
            self._json(400, {"status": "error", "error": "invalid delta"})
            return
        seeking = seek_relative(delta)
        self._json(200, {"status": "seeking" if seeking else "idle"})

    def _handle_play(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"status": "error", "error": "invalid JSON"})
            return

        title = payload.get("title") or "Unknown"
        episode = payload.get("episode", 1)
        audio = payload.get("audio", "sub")  # "sub" | "dub"
        # M7A: AniList id keys the progress store; `media` is a lightweight
        # snapshot the home/detail UI can re-render from (used by Continue Watching).
        anilist_id = payload.get("anilistId", payload.get("id"))
        media = payload.get("media")
        # A direct watch URL may be passed via "url" for manual testing; normally
        # we search by the bare title (the episode number is selected separately).
        target = payload.get("url")
        print(f"[play] request: {title!r} ep {episode} ({audio})")

        try:
            stream = scraper.get_stream_url(target, title=title, episode=episode, audio=audio)
        except scraper.ScrapeError as e:
            print(f"[play] scrape failed: {e}", file=sys.stderr)
            self._json(502, {"status": "error", "error": str(e)})
            return

        print(f"[play] resolved -> {stream.page}")
        result = play_blocking(stream, title, episode,
                               anilist_id=anilist_id, media=media, audio=audio)
        code = 200 if result.get("status") in ("ended", "busy") else 502
        self._json(code, result)

    def do_OPTIONS(self):  # CORS preflight (lets a file:// dev page POST here)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        # Never let the kiosk cache the UI — otherwise a `git pull` can leave the
        # browser running a stale mix of old HTML + new JS until a hard reload.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep stdout focused on IR/play events


def start_http_server():
    """Serve the frontend (and /play) on a background daemon thread."""
    server = http.server.ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), FrontendHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[http] serving {FRONTEND_DIR} on http://localhost:{HTTP_PORT}")
    return server


async def router_reader(socket_path):
    """Connect to arduino-router, register ir_command, broadcast what arrives."""
    if msgpack is None:
        print("msgpack not installed: sudo apt install python3-msgpack", file=sys.stderr)
        return
    while True:
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
        except (FileNotFoundError, ConnectionError, OSError) as e:
            print(f"[router] cannot connect to {socket_path}: {e}; retrying in 3s",
                  file=sys.stderr)
            await asyncio.sleep(3)
            continue

        # Register the method the firmware notifies: [REQUEST, id, "$/register", ["ir_command"]]
        writer.write(msgpack.packb([REQUEST, 1, "$/register", [IR_METHOD]], use_bin_type=True))
        await writer.drain()
        print(f"[router] connected to {socket_path}, registered '{IR_METHOD}'")

        unpacker = msgpack.Unpacker(raw=False)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    print("[router] connection closed; reconnecting", file=sys.stderr)
                    break
                unpacker.feed(data)
                for msg in unpacker:
                    await handle_router_message(msg, writer)
        except (ConnectionError, OSError) as e:
            print(f"[router] connection error: {e}; reconnecting", file=sys.stderr)
        finally:
            writer.close()
        await asyncio.sleep(2)


async def handle_router_message(msg, writer):
    """Dispatch one decoded MessagePack-RPC message from the router."""
    if not isinstance(msg, (list, tuple)) or not msg:
        return
    msgtype = msg[0]

    if msgtype == RESPONSE:           # [RESPONSE, id, error, result] -- e.g. our register ack
        error = msg[2] if len(msg) > 2 else None
        if error is not None:
            print(f"[router] error response: {error}", file=sys.stderr)
        return

    if msgtype == REQUEST:            # [REQUEST, id, method, params] -- MCU used Bridge.call
        _, msgid, method, params = (list(msg) + [None] * 4)[:4]
        if method == IR_METHOD:
            broadcast(params[0] if params else None)
        writer.write(msgpack.packb([RESPONSE, msgid, None, True], use_bin_type=True))
        await writer.drain()
        return

    if msgtype == NOTIFICATION:       # [NOTIFICATION, method, params] -- MCU used Bridge.notify
        _, method, params = (list(msg) + [None] * 3)[:3]
        if method == IR_METHOD:
            broadcast(params[0] if params else None)
        return


async def stdin_reader():
    """--mock mode: read commands from the terminal instead of the hardware."""
    loop = asyncio.get_running_loop()
    print("[mock] type a command (UP/DOWN/LEFT/RIGHT/OK/BACK) and press Enter")
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break  # EOF
        broadcast(line.strip().upper())


async def main():
    parser = argparse.ArgumentParser(description="openAnime router -> WebSocket bridge")
    parser.add_argument("--router", default=ROUTER_SOCKET,
                        help=f"arduino-router Unix socket (default: {ROUTER_SOCKET})")
    parser.add_argument("--mock", action="store_true",
                        help="read commands from stdin instead of the router")
    args = parser.parse_args()

    start_http_server()
    async with websockets.serve(register, WS_HOST, WS_PORT):
        print(f"[ws] listening on ws://localhost:{WS_PORT}")
        if args.mock:
            await stdin_reader()
        else:
            await router_reader(args.router)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
