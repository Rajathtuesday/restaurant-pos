#!/usr/bin/env python3
"""
Rasova Print Agent
==================
Lightweight WebSocket bridge from the browser to a local USB thermal printer.
No Java. No certificates. No third-party dependencies beyond what Rasova already uses.

Usage:
    python rasova_agent.py              # run the agent
    python rasova_agent.py --install    # auto-start at Windows login (run once)
    python rasova_agent.py --uninstall  # remove auto-start

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
import logging.handlers
import sys
import os
import time
from typing import Optional

HOST = "localhost"
PORT = 8765
VERSION = "1.0.1"

IS_WINDOWS = sys.platform == "win32"

# ── Logging ───────────────────────────────────────────────────────────────────
# Log to file always (agent may run hidden with no console window)
# Log to console only when running interactively

def _setup_logging():
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _log = logging.getLogger("rasova.agent")
    _log.setLevel(logging.INFO)

    # Console handler (only useful when window is visible)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    _log.addHandler(ch)

    # File handler — always active, even when running hidden
    log_dir = os.path.join(os.environ.get("APPDATA", "."), "Rasova")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "agent.log")
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    _log.addHandler(fh)

    return _log, log_path

logger, LOG_PATH = _setup_logging()

# ── Windows printer access ────────────────────────────────────────────────────
_win32print = None
if IS_WINDOWS:
    try:
        import win32print as _win32print
        logger.info("Windows printing available via win32print")
    except ImportError:
        logger.warning("pywin32 not installed — run: pip install pywin32")

# ── ESC/POS over network (fallback / LAN printers) ───────────────────────────
try:
    from escpos.printer import Network as _NetworkPrinter
except ImportError:
    _NetworkPrinter = None


# ── Auto-start install / uninstall ───────────────────────────────────────────

def _startup_dir() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )

def _vbs_path() -> str:
    return os.path.join(_startup_dir(), "RasovaPrintAgent.vbs")


def install_autostart():
    """
    Drop a VBS launcher into the Windows Startup folder.
    Runs silently (no console window) every time the user logs in.
    No admin rights required.
    """
    if not IS_WINDOWS:
        print("Auto-start via Startup folder is Windows-only.")
        print("On Mac: add to Login Items in System Settings.")
        sys.exit(0)

    agent_path = os.path.abspath(__file__)

    # Prefer pythonw.exe — runs with no console window
    python_exe = sys.executable
    pythonw = python_exe.replace("python.exe", "pythonw.exe")
    if os.path.exists(pythonw):
        python_exe = pythonw

    # VBS launches the agent silently (window style 0 = hidden)
    vbs = (
        f'CreateObject("Wscript.Shell").Run '
        f'"""{python_exe}"" ""{agent_path}""", 0, False\n'
    )

    vbs_file = _vbs_path()
    try:
        with open(vbs_file, "w", encoding="utf-8") as f:
            f.write(vbs)
        print(f"✓  Auto-start installed.")
        print(f"   Rasova Agent will start silently at every Windows login.")
        print(f"   Launcher: {vbs_file}")
        print(f"   Logs:     {LOG_PATH}")
        print()
        print("   To verify it's running: open Kitchen Stations — look for green dot.")
        print("   To remove auto-start:   python rasova_agent.py --uninstall")
    except Exception as e:
        print(f"✗  Failed to write startup file: {e}")
        print(f"   Try running as Administrator or manually copy to:")
        print(f"   {_startup_dir()}")
        sys.exit(1)


def uninstall_autostart():
    """Remove the VBS launcher from the Windows Startup folder."""
    vbs_file = _vbs_path()
    if os.path.exists(vbs_file):
        os.remove(vbs_file)
        print("✓  Auto-start removed. Rasova Agent will no longer start at login.")
    else:
        print("   Auto-start was not installed (nothing to remove).")


# ── Printer utilities ─────────────────────────────────────────────────────────

def list_windows_printers() -> list:
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
    all_printers = list_windows_printers()
    name_lower = name.lower()
    for p in all_printers:
        if p.lower() == name_lower:
            return p
    for p in all_printers:
        if name_lower in p.lower():
            return p
    return None


def print_raw_windows(printer_name: str, data: bytes, retries: int = 3) -> tuple:
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

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await websocket.send(json.dumps({
                    "type": "pong",
                    "version": VERSION,
                    "platform": sys.platform,
                    "win32print": _win32print is not None,
                    "log_path": LOG_PATH,
                }))

            elif msg_type == "list_printers":
                printers = list_windows_printers()
                await websocket.send(json.dumps({
                    "type": "printers",
                    "list": printers,
                    "default": _win32print.GetDefaultPrinter() if _win32print else None,
                }))

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
    logger.info("  Log file: %s", LOG_PATH)
    if IS_WINDOWS:
        printers = list_windows_printers()
        if printers:
            logger.info("  Printers: %s", ", ".join(printers))
        else:
            logger.warning("  No printers found — connect BillTouch via USB")
    logger.info("=" * 56)

    async with websockets.serve(
        handle_client, HOST, PORT,
        origins=None,
        ping_interval=30,
        ping_timeout=10,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    # ── CLI flags ─────────────────────────────────────────────────────────────
    if "--install" in sys.argv:
        install_autostart()
        sys.exit(0)

    if "--uninstall" in sys.argv:
        uninstall_autostart()
        sys.exit(0)

    # ── Normal run ────────────────────────────────────────────────────────────
    try:
        import websockets
    except ImportError:
        logger.error("websockets not installed — run: pip install websockets")
        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Rasova Print Agent stopped.")
