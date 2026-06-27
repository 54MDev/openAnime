#!/usr/bin/env python3
"""openAnime backend bridge.

Reads named IR commands from the STM32 over the internal serial bridge and
broadcasts each one to every connected browser client over a WebSocket.

The firmware (firmware/firmware/firmware.ino) prints one command per line at
9600 baud: UP, DOWN, LEFT, RIGHT, OK, BACK.

Run:
    python3 backend/app.py                 # read from the serial bridge
    python3 backend/app.py --mock          # type commands by hand (no hardware)

The serial device differs per board; override it with --serial or the
OPENANIME_SERIAL env var. Common values: /dev/ttyS0, /dev/ttyAMA0, /dev/ttyUSB0.
"""

import argparse
import asyncio
import os
import sys

import websockets

try:
    import serial  # pyserial
except ImportError:
    serial = None

WS_HOST = "0.0.0.0"
WS_PORT = 8765
BAUD = 9600
DEFAULT_SERIAL = os.environ.get("OPENANIME_SERIAL", "/dev/ttyS0")
VALID_COMMANDS = {"UP", "DOWN", "LEFT", "RIGHT", "OK", "BACK"}

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
    """Send one command string to all connected clients."""
    if not clients:
        print(f"[ir] {command} (no clients connected)")
        return
    print(f"[ir] {command} -> {len(clients)} client(s)")
    websockets.broadcast(clients, command)


async def serial_reader(loop, port):
    """Read command lines off the serial bridge and broadcast valid ones."""
    if serial is None:
        print("pyserial not installed: pip3 install pyserial", file=sys.stderr)
        return
    while True:
        try:
            ser = serial.Serial(port, BAUD, timeout=1)
        except serial.SerialException as e:
            print(f"[serial] cannot open {port}: {e}; retrying in 3s", file=sys.stderr)
            await asyncio.sleep(3)
            continue
        print(f"[serial] reading {port} @ {BAUD} baud")
        try:
            while True:
                # pyserial is blocking; read in a thread so the event loop runs.
                line = await loop.run_in_executor(None, ser.readline)
                if not line:
                    continue
                command = line.decode(errors="ignore").strip()
                if command in VALID_COMMANDS:
                    broadcast(command)
                elif command:
                    print(f"[serial] ignored unrecognized line: {command!r}")
        except serial.SerialException as e:
            print(f"[serial] lost {port}: {e}; reopening", file=sys.stderr)
            await asyncio.sleep(2)
        finally:
            ser.close()


async def stdin_reader(loop):
    """--mock mode: read commands from the terminal instead of the hardware."""
    print("[mock] type a command (UP/DOWN/LEFT/RIGHT/OK/BACK) and press Enter")
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break  # EOF
        command = line.strip().upper()
        if command in VALID_COMMANDS:
            broadcast(command)
        elif command:
            print(f"[mock] unknown command: {command!r}")


async def main():
    parser = argparse.ArgumentParser(description="openAnime serial -> WebSocket bridge")
    parser.add_argument("--serial", default=DEFAULT_SERIAL,
                        help=f"serial device path (default: {DEFAULT_SERIAL})")
    parser.add_argument("--mock", action="store_true",
                        help="read commands from stdin instead of the serial bridge")
    args = parser.parse_args()

    loop = asyncio.get_running_loop()
    async with websockets.serve(register, WS_HOST, WS_PORT):
        print(f"[ws] listening on ws://localhost:{WS_PORT}")
        if args.mock:
            await stdin_reader(loop)
        else:
            await serial_reader(loop, args.serial)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
