#!/usr/bin/env python3
"""
Rasova Print Agent
==================
Lightweight WebSocket bridge from the browser to a local USB/network thermal printer.
No Java. No certificates. No third-party dependencies beyond what Rasova already uses.

Usage:
    python rasova_agent.py              # run the agent
    python rasova_agent.py --install    # auto-start at Windows login (run once)
    python rasova_agent.py --uninstall  # remove auto-start

The browser connects to: ws://localhost:8765

Protocol (JSON over WebSocket):
  Browser -> Agent:
    {"type": "ping"}
    {"type": "list_printers"}
    {"type": "print", "printer": "BillTouch ZY306", "lines": [...ESC/POS strings...]}
    {"type": "print", "network_host": "192.168.1.101", "network_port": 9100, "lines": [...], "outlet_id": "my-outlet"}
    {"type": "discover_network_printers"}
    {"type": "save_printer_config", "outlet_id": "my-outlet", "usb_name": "...", "network_ip": "...", "network_port": 9100}
    {"type": "get_config"}

  Agent -> Browser:
    {"type": "pong", "version": "1.1.0"}
    {"type": "printers", "list": ["BillTouch ZY306", ...]}
    {"type": "ok", "message": "Printed"}
    {"type": "error", "message": "Printer offline"}
    {"type": "network_printers", "list": [{"ip": "192.168.1.101", "port": 9100, "responded_ms": 45}, ...]}
    {"type": "config", "data": {...}}
"""

import asyncio
import json
import logging
import logging.handlers
import sys
import os
import socket
import subprocess
import time
from datetime import datetime
from typing import Optional

HOST = "localhost"
PORT = 8765
VERSION = "1.1.0"

IS_WINDOWS = sys.platform == "win32"

# ── Config paths ──────────────────────────────────────────────────────────────

def _config_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~")
        return os.path.join(base, ".rasova")
    return os.path.join(base, "Rasova")

CONFIG_DIR = _config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "agent_config.json")

# ── Logging ───────────────────────────────────────────────────────────────────
# Log to file always (agent may run hidden with no console window)
# Log to console only when running interactively

def _setup_logging():
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _log = logging.getLogger("rasova.agent")
    _log.setLevel(logging.INFO)

    # Console handler (only useful when window is visible)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    _log.addHandler(ch)

    # File handler -- always active, even when running hidden
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
        logger.warning("pywin32 not installed -- run: pip install pywin32")

# ── ESC/POS over network (fallback / LAN printers) ───────────────────────────
try:
    from escpos.printer import Network as _NetworkPrinter
except ImportError:
    _NetworkPrinter = None


# ── Config persistence ────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load agent config from disk. Returns empty config if file missing or corrupt."""
    if not os.path.exists(CONFIG_PATH):
        return {"version": 1, "printers": {}}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "printers" not in data:
            return {"version": 1, "printers": {}}
        return data
    except Exception as e:
        logger.warning("Could not load config from %s: %s", CONFIG_PATH, e)
        return {"version": 1, "printers": {}}


def _save_config(config: dict) -> bool:
    """Persist config to disk. Returns True on success."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
        return True
    except Exception as e:
        logger.error("Could not save config to %s: %s", CONFIG_PATH, e)
        return False


def _update_last_seen(config: dict, outlet_id: str, ip: str) -> dict:
    """Update last_seen_ip and last_seen timestamp for an outlet in config."""
    if not outlet_id:
        return config
    printers = config.setdefault("printers", {})
    entry = printers.setdefault(outlet_id, {})
    entry["last_seen_ip"] = ip
    entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
    return config


# Global config loaded at startup
_agent_config: dict = {}


# ── MAC → IP resolution via ARP ──────────────────────────────────────────────

def _normalise_mac(mac: str) -> str:
    """Normalise any MAC format to lowercase colon-separated: aa:bb:cc:dd:ee:ff"""
    digits = mac.replace(":", "").replace("-", "").replace(".", "").lower()
    return ":".join(digits[i:i+2] for i in range(0, 12, 2))


def resolve_mac_to_ip(mac: str) -> Optional[str]:
    """
    Parse the OS ARP table to find the current IP for a given MAC address.
    Works on Windows, Linux, and macOS without any extra dependencies.
    Returns the IP string or None if not found.
    """
    if not mac:
        return None

    target = _normalise_mac(mac)

    try:
        if IS_WINDOWS:
            result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        else:
            result = subprocess.run(["arp", "-n"], capture_output=True, text=True, timeout=5)
        output = result.stdout
    except Exception as e:
        logger.warning("ARP lookup failed: %s", e)
        return None

    import re
    # Match lines like: 192.168.1.5   00-1b-44-11-3a-b7   dynamic
    #               or: 192.168.1.5   00:1b:44:11:3a:b7   (Linux)
    for line in output.splitlines():
        # Extract any MAC-like token on the line
        macs_on_line = re.findall(r"([0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2})", line)
        for found_mac in macs_on_line:
            if _normalise_mac(found_mac) == target:
                # Extract the IP from the same line (first IPv4 address)
                ip_match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", line)
                if ip_match:
                    ip = ip_match.group(1)
                    logger.info("ARP resolved MAC %s → %s", target, ip)
                    return ip

    logger.info("ARP: MAC %s not found in table (printer may be offline or ARP cache stale)", target)
    return None


# ── Network printer discovery ─────────────────────────────────────────────────

def _get_local_subnet() -> Optional[str]:
    """
    Detect the machine's primary LAN IP and return the /24 subnet prefix.
    E.g. if IP is 192.168.1.42, returns "192.168.1".
    Returns None if no non-loopback IP found.
    """
    try:
        # Connect to a public IP (doesn't actually send data) to find the
        # outbound interface's local address.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        parts = ip.split(".")
        if len(parts) == 4 and not ip.startswith("127."):
            return ".".join(parts[:3])
    except Exception as e:
        logger.warning("Could not detect local subnet: %s", e)
    return None


async def _probe_port(ip: str, port: int, timeout: float) -> Optional[dict]:
    """
    Try to open a TCP connection to ip:port within `timeout` seconds.
    Returns a result dict on success, None on failure.
    """
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return {"ip": ip, "port": port, "responded_ms": elapsed_ms}
    except Exception:
        return None


async def discover_network_printers(port: int = 9100, timeout: float = 0.5) -> list:
    """
    Scan all 254 hosts on the local /24 subnet for open port 9100.
    Returns list of dicts: [{"ip": "...", "port": 9100, "responded_ms": 45}, ...]
    Completes in ~timeout seconds wall-clock time via asyncio.gather.
    """
    subnet = _get_local_subnet()
    if not subnet:
        logger.warning("Network discovery: could not detect local subnet")
        return []

    logger.info("Scanning %s.1-%s.254 port %d ...", subnet, subnet, port)
    tasks = [
        _probe_port(f"{subnet}.{i}", port, timeout)
        for i in range(1, 255)
    ]
    results = await asyncio.gather(*tasks)
    found = [r for r in results if r is not None]
    found.sort(key=lambda x: x["responded_ms"])
    logger.info("Network discovery found %d printer(s): %s",
                len(found), [r["ip"] for r in found])
    return found


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
    Also pip-installs required dependencies.
    """
    if not IS_WINDOWS:
        print("Auto-start via Startup folder is Windows-only.")
        print("On Mac: add to Login Items in System Settings.")
        sys.exit(0)

    # Step 1: Install dependencies
    print("Installing dependencies...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "websockets", "pywin32"],
        check=False,
    )
    print("  Dependencies installed (websockets, pywin32).")

    # Step 2: Write VBS launcher
    agent_path = os.path.abspath(__file__)

    # Prefer pythonw.exe -- runs with no console window
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
        print(f"  Auto-start installed.")
        print(f"  Rasova Agent will start silently at every Windows login.")
        print(f"  Launcher: {vbs_file}")
        print(f"  Logs:     {LOG_PATH}")
        print()
        print("  To verify it's running: open Kitchen Stations -- look for green dot.")
        print("  To remove auto-start:   python rasova_agent.py --uninstall")
    except Exception as e:
        print(f"  Failed to write startup file: {e}")
        print(f"  Try running as Administrator or manually copy to:")
        print(f"  {_startup_dir()}")
        sys.exit(1)


def uninstall_autostart():
    """Remove the VBS launcher from the Windows Startup folder."""
    vbs_file = _vbs_path()
    if os.path.exists(vbs_file):
        os.remove(vbs_file)
        print("  Auto-start removed. Rasova Agent will no longer start at login.")
    else:
        print("  Auto-start was not installed (nothing to remove).")


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
        return False, "pywin32 not installed -- run: pip install pywin32"

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

    return False, f"Print failed after {retries} attempts -- is printer on and connected?"


def print_raw_network(host: str, port: int, data: bytes) -> tuple:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((host, port))
            s.sendall(data)
        logger.info("Sent %d bytes to %s:%d", len(data), host, port)
        return True, f"Sent to {host}:{port}"
    except Exception as e:
        return False, f"Network print failed ({host}:{port}): {e}"


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


# ── Smart network print with re-discovery ─────────────────────────────────────

async def smart_network_print(
    net_host: str,
    net_port: int,
    data: bytes,
    outlet_id: str,
    config: dict,
    printer_mac: str = "",
) -> tuple:
    """
    Try network printing with progressive fallback:
    1. Provided IP (normal path)
    2. ARP resolution of MAC address (permanent identifier — survives DHCP changes)
    3. Cached last_seen_ip for this outlet
    4. Full subnet scan, try each discovered IP until one works
    Returns (success, message, working_ip_or_None)
    """
    tried = []

    # Level 1: try the provided IP
    if net_host:
        tried.append(net_host)
        success, msg = print_raw_network(net_host, net_port, data)
        if success:
            return True, msg, net_host

    # Level 2: ARP-resolve the MAC address (works even after DHCP change/router reset)
    if printer_mac:
        arp_ip = resolve_mac_to_ip(printer_mac)
        if arp_ip and arp_ip not in tried:
            tried.append(arp_ip)
            logger.info("Trying ARP-resolved IP for MAC %s: %s", printer_mac, arp_ip)
            success, msg = print_raw_network(arp_ip, net_port, data)
            if success:
                return True, f"{msg} (found via MAC address)", arp_ip

    # Level 3: try cached last_seen_ip (if different from already tried)
    cached_ip = None
    if outlet_id:
        outlet_cfg = config.get("printers", {}).get(outlet_id, {})
        cached_ip = outlet_cfg.get("last_seen_ip", "")
    if cached_ip and cached_ip not in tried:
        tried.append(cached_ip)
        logger.info("Trying cached IP for outlet '%s': %s", outlet_id, cached_ip)
        success, msg = print_raw_network(cached_ip, net_port, data)
        if success:
            return True, msg, cached_ip

    # Level 4: full subnet discovery
    logger.info("Network print failed on all known IPs, starting subnet discovery...")
    discovered = await discover_network_printers(port=net_port, timeout=0.5)
    for printer_info in discovered:
        candidate = printer_info["ip"]
        if candidate in tried:
            continue
        tried.append(candidate)
        logger.info("Trying discovered printer at %s", candidate)
        success, msg = print_raw_network(candidate, net_port, data)
        if success:
            return True, f"{msg} (re-discovered after IP change)", candidate

    detail = ", ".join(tried) if tried else "none"
    return False, f"Network print failed. Tried: {detail}", None


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handle_client(websocket):
    global _agent_config
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

            # ── ping ──────────────────────────────────────────────────────────
            if msg_type == "ping":
                await websocket.send(json.dumps({
                    "type": "pong",
                    "version": VERSION,
                    "platform": sys.platform,
                    "win32print": _win32print is not None,
                    "log_path": LOG_PATH,
                }))

            # ── list_printers ─────────────────────────────────────────────────
            elif msg_type == "list_printers":
                printers = list_windows_printers()
                await websocket.send(json.dumps({
                    "type": "printers",
                    "list": printers,
                    "default": _win32print.GetDefaultPrinter() if _win32print else None,
                }))

            # ── discover_network_printers ─────────────────────────────────────
            elif msg_type == "discover_network_printers":
                port = int(msg.get("port", 9100))
                timeout = float(msg.get("timeout", 0.5))
                found = await discover_network_printers(port=port, timeout=timeout)
                await websocket.send(json.dumps({
                    "type": "network_printers",
                    "list": found,
                }))

            # ── save_printer_config ───────────────────────────────────────────
            elif msg_type == "save_printer_config":
                outlet_id = msg.get("outlet_id", "").strip()
                if not outlet_id:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "outlet_id is required for save_printer_config",
                    }))
                    continue

                printers = _agent_config.setdefault("printers", {})
                entry = printers.setdefault(outlet_id, {})
                for field in ("usb_name", "network_ip", "network_port", "last_seen_ip", "last_seen"):
                    if field in msg:
                        entry[field] = msg[field]

                saved = _save_config(_agent_config)
                await websocket.send(json.dumps({
                    "type": "ok" if saved else "error",
                    "message": "Config saved" if saved else "Config save failed (check logs)",
                }))

            # ── get_config ────────────────────────────────────────────────────
            elif msg_type == "get_config":
                await websocket.send(json.dumps({
                    "type": "config",
                    "data": _agent_config,
                    "config_path": CONFIG_PATH,
                }))

            # ── print ─────────────────────────────────────────────────────────
            elif msg_type == "print":
                printer     = msg.get("printer", "").strip()
                lines       = msg.get("lines", [])
                net_host    = msg.get("network_host", "").strip()
                net_port    = int(msg.get("network_port", 9100))
                encoding    = msg.get("encoding", "cp437")
                job_id      = msg.get("job_id", "")
                outlet_id   = msg.get("outlet_id", "").strip()
                printer_mac = msg.get("printer_mac", "").strip()

                if not lines:
                    await websocket.send(json.dumps({
                        "type": "error", "job_id": job_id,
                        "message": "No print data provided",
                    }))
                    continue

                data = decode_lines(lines, encoding)
                working_ip = None

                if net_host or printer_mac:
                    # Smart network print with fallback chain (MAC→ARP → cached IP → subnet scan)
                    success, message, working_ip = await smart_network_print(
                        net_host, net_port, data, outlet_id, _agent_config, printer_mac
                    )
                elif printer:
                    success, message = print_raw_windows(printer, data)
                else:
                    success, message = False, "No printer name or network host provided"

                # Save successful IP to config
                if success and working_ip and outlet_id:
                    _update_last_seen(_agent_config, outlet_id, working_ip)
                    _save_config(_agent_config)

                await websocket.send(json.dumps({
                    "type": "ok" if success else "error",
                    "job_id": job_id,
                    "message": message,
                }))

            else:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
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
                sys.platform, "yes" if _win32print else "no (install pywin32)")
    logger.info("  Log file: %s", LOG_PATH)
    logger.info("  Config:   %s", CONFIG_PATH)
    if IS_WINDOWS:
        printers = list_windows_printers()
        if printers:
            logger.info("  Printers: %s", ", ".join(printers))
        else:
            logger.warning("  No printers found -- connect BillTouch via USB")
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
        logger.error("websockets not installed -- run: pip install websockets")
        sys.exit(1)

    # Load config on startup
    _agent_config = _load_config()
    logger.info("Loaded config from %s (%d outlet(s))",
                CONFIG_PATH, len(_agent_config.get("printers", {})))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Rasova Print Agent stopped.")
