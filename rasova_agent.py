#!/usr/bin/env python3
"""
Rasova Print Agent
==================
Lightweight WebSocket bridge from the browser to a local USB thermal printer.
No Java. No certificates. No third-party dependencies beyond what Rasova already uses.

Usage:
    python rasova_agent.py

The browser connects to: ws://localhost:8765

Protocol (JSON over WebSocket):
  Browser → Agent:
    {"type": "ping"}
    {"type": "list_printers"}
    {"type": "print", "printer": "BillTouch ZY306", "lines": [...ESC/POS strings...]}

  Agent → Browser:
    {"type": "pong", "version": "1.0"}
    {"type": "printers", "list": ["BillTouch ZY306", ...]}
    {"type": "ok", "message": "Printed"}
    {"type": "error", "message": "Printer offline"}
"""

import asyncio
import json
import logging
import sys
import os
import time
import threading
from typing import Optional

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rasova.agent")

HOST = "localhost"
PORT = 8765
VERSION = "1.0.0"

# ── Platform detection ────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

# ── Windows printer access ────────────────────────────────────────────────────
_win32print = None
if IS_WINDOWS:
    try:
        import win32print as _win32print
        logger.info("Windows printing available via win32print")
    except ImportError:
        logger.warning("pywin32 not installed — install with: pip install pywin32")

# ── ESC/POS over network (fallback / LAN printers) ───────────────────────────
_network_printer = None
try:
    from escpos.printer import Network as _NetworkPrinter
except ImportError:
    _NetworkPrinter = None


# ── Printer utilities ─────────────────────────────────────────────────────────

def list_windows_printers() -> list:
    """Return all installed Windows printer names."""
    if not _win32print:
        return []
    try:
        printers = _win32print.EnumPrinters(
            _win32print.PRINTER_ENUM_LOCAL | _win32print.PRINTER_ENUM_CONNECTIONS
        )
        return [p[2] for p in printers]
    except Exception as e:
        logger.error("Could not list printers: %s", e)
        return []


def find_printer(name: str) -> Optional[str]:
    """
    Find a printer by name (case-insensitive, partial match).
    Returns the exact Windows printer name or None.
    """
    all_printers = list_windows_printers()
    name_lower = name.lower()
    # Exact match first
    for p in all_printers:
        if p.lower() == name_lower:
            return p
    # Partial match
    for p in all_printers:
        if name_lower in p.lower():
            return p
    return None


def print_raw_windows(printer_name: str, data: bytes, retries: int = 3) -> tuple:
    """
    Send raw ESC/POS bytes to a Windows USB printer.
    Returns (success: bool, message: str)
    """
    if not _win32print:
        return False, "pywin32 not installed — run: pip install pywin32"

    exact_name = find_printer(printer_name)
    if not exact_name:
        available = list_windows_printers()
        msg = f"Printer '{printer_name}' not found. Available: {', '.join(available) or 'none'}"
        return False, msg

    for attempt in range(retries):
        try:
            h = _win32print.OpenPrinter(exact_name)
            try:
                _win32print.StartDocPrinter(h, 1, ("Rasova Receipt", None, "RAW"))
                _win32print.StartPagePrinter(h)
                _win32print.WritePrinter(h, data)
                _win32print.EndPagePrinter(h)
                _win32print.EndDocPrinter(h)
            finally:
                _win32print.ClosePrinter(h)
            logger.info("Printed %d bytes to '%s'", len(data), exact_name)
            return True, f"Printed to {exact_name}"
        except Exception as e:
            logger.warning("Print attempt %d/%d failed: %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(1)

    return False, f"Print failed after {retries} attempts — is printer on and connected?"


def print_raw_network(host: str, port: int, data: bytes) -> tuple:
    """Send raw ESC/POS to a network printer (TCP:9100)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((host, port))
            s.sendall(data)
        logger.info("Sent %d bytes to %s:%d", len(data), host, port)
        return True, f"Sent to {host}:{port}"
    except Exception as e:
        return False, f"Network print failed: {e}"


def decode_lines(lines: list, encoding: str = "cp437") -> bytes:
    """
    Convert the list of ESC/POS string commands to bytes.
    Handles Unicode → bytes safely for thermal printers.
    """
    result = b""
    for line in lines:
        if isinstance(line, str):
            try:
                result += line.encode(encoding, errors="replace")
            except LookupError:
                result += line.encode("cp437", errors="replace")
        elif isinstance(line, (bytes, bytearray)):
            result += bytes(line)
    return result


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handle_client(websocket):
    client = websocket.remote_address
    logger.info("Browser connected: %s", client)

    # Tell browser we support CORS / all origins
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            msg_type = msg.get("type", "")

            # ── Ping / health check ──────────────────────────────────────
            if msg_type == "ping":
                await websocket.send(json.dumps({
                    "type": "pong",
                    "version": VERSION,
                    "platform": sys.platform,
                    "win32print": _win32print is not None,
                }))

            # ── List available printers ──────────────────────────────────
            elif msg_type == "list_printers":
                printers = list_windows_printers()
                await websocket.send(json.dumps({
                    "type": "printers",
                    "list": printers,
                    "default": _win32print.GetDefaultPrinter() if _win32print else None,
                }))

            # ── Print job ────────────────────────────────────────────────
            elif msg_type == "print":
                printer  = msg.get("printer", "").strip()
                lines    = msg.get("lines", [])
                net_host = msg.get("network_host", "")
                net_port = int(msg.get("network_port", 9100))
                encoding = msg.get("encoding", "cp437")
                job_id   = msg.get("job_id", "")

                if not lines:
                    await websocket.send(json.dumps({
                        "type": "error", "job_id": job_id,
                        "message": "No print data provided"
                    }))
                    continue

                data = decode_lines(lines, encoding)

                # Try network first if host provided, else Windows USB
                if net_host:
                    success, message = print_raw_network(net_host, net_port, data)
                elif printer:
                    success, message = print_raw_windows(printer, data)
                else:
                    success, message = False, "No printer name or network host provided"

                await websocket.send(json.dumps({
                    "type": "ok" if success else "error",
                    "job_id": job_id,
                    "message": message,
                }))

            else:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}"
                }))

    except Exception as e:
        logger.warning("Client %s disconnected: %s", client, e)
    finally:
        logger.info("Browser disconnected: %s", client)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    import websockets

    logger.info("=" * 56)
    logger.info("  Rasova Print Agent v%s", VERSION)
    logger.info("  Listening on ws://%s:%d", HOST, PORT)
    logger.info("  Platform: %s | win32print: %s",
                sys.platform, "✓" if _win32print else "✗ (install pywin32)")
    if IS_WINDOWS:
        printers = list_windows_printers()
        if printers:
            logger.info("  Printers found: %s", ", ".join(printers))
        else:
            logger.warning("  No printers found — connect BillTouch via USB")
    logger.info("=" * 56)
    logger.info("  In Rasova: Kitchen Stations → enable 'Rasova Agent'")
    logger.info("  Keep this window open while billing.")
    logger.info("  Press Ctrl+C to stop.")
    logger.info("")

    async with websockets.serve(
        handle_client, HOST, PORT,
        origins=None,          # accept connections from any origin (localhost only anyway)
        ping_interval=30,
        ping_timeout=10,
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        # Check websockets is available
        import websockets
    except ImportError:
        logger.error("websockets not installed — run: pip install websockets")
        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Rasova Print Agent stopped.")
