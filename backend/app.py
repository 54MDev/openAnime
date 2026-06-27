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
import os
import sys

import websockets

try:
    import msgpack
except ImportError:
    msgpack = None

WS_HOST = "0.0.0.0"
WS_PORT = 8765
ROUTER_SOCKET = os.environ.get("OPENANIME_ROUTER", "/var/run/arduino-router.sock")
IR_METHOD = "ir_command"
VALID_COMMANDS = {"UP", "DOWN", "LEFT", "RIGHT", "OK", "BACK"}

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
