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
import subprocess
import sys
import threading
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


def play_blocking(stream, title, episode):
    """Launch mpv and block until it exits. Returns a status dict for the UI.

    `stream` is a scraper.Stream (url + http_headers). The extracted .m3u8 is
    referer-gated, so Referer/User-Agent from the scraper must be forwarded or
    the CDN 403s.
    """
    global _mpv_proc
    cmd = [MPV_BIN, "--fullscreen", "--ontop", "--no-terminal", "--really-quiet",
           f"--force-media-title={title} — Episode {episode}"]
    if AUDIO_DEVICE:
        cmd.append(f"--audio-device={AUDIO_DEVICE}")
    # Forward the gating headers. (Only referer + UA; other headers like Accept
    # contain commas, which mpv's --http-header-fields would mis-split.)
    for key, value in (stream.headers or {}).items():
        if key.lower() == "referer":
            cmd.append(f"--referrer={value}")
        elif key.lower() == "user-agent":
            cmd.append(f"--user-agent={value}")
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
    rc = proc.wait()  # blocks here for the whole session (lock released)

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


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the frontend/ directory and handles the placeholder /play POST.

    Static files (index.html, style.css, app.js) are served straight from
    FRONTEND_DIR. POST /play resolves a stream and blocks for the viewing
    session; POST /stop ends the current playback.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/play":
            self._handle_play()
        elif path == "/stop":
            stopped = stop_playback()
            self._json(200, {"status": "stopping" if stopped else "idle"})
        else:
            self._json(404, {"error": "not found"})

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
        # A direct watch URL may be passed via "url" for manual testing; normally
        # we search by the bare title (the episode number is selected separately).
        target = payload.get("url")
        print(f"[play] request: {title!r} ep {episode}")

        try:
            stream = scraper.get_stream_url(target, title=title, episode=episode)
        except scraper.ScrapeError as e:
            print(f"[play] scrape failed: {e}", file=sys.stderr)
            self._json(502, {"status": "error", "error": str(e)})
            return

        print(f"[play] resolved -> {stream.page}")
        result = play_blocking(stream, title, episode)
        code = 200 if result.get("status") in ("ended", "busy") else 502
        self._json(code, result)

    def do_OPTIONS(self):  # CORS preflight (lets a file:// dev page POST here)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
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
