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

HOST = "127.0.0.1"
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
    os.makedirs(CONFIG_DIR, exist_ok=True)
    log_path = os.path.join(CONFIG_DIR, "agent.log")
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


def install_autostart_termux():
    """
    Set up Rasova Agent to auto-start on Android via Termux:Boot.
    Requires the Termux:Boot app to be installed from F-Droid.
    """
    boot_dir = os.path.expanduser("~/.termux/boot")
    os.makedirs(boot_dir, exist_ok=True)

    agent_path = os.path.abspath(__file__)
    boot_script = os.path.join(boot_dir, "rasova_agent.sh")

    script = (
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        "# Auto-generated by rasova_agent.py --install\n"
        "# Starts Rasova Print Agent on Termux boot\n"
        f"cd {os.path.dirname(agent_path)}\n"
        f"nohup python {agent_path} >> {CONFIG_DIR}/agent.log 2>&1 &\n"
    )

    with open(boot_script, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(boot_script, 0o755)

    print("Termux auto-start installed.")
    print(f"  Boot script: {boot_script}")
    print(f"  Logs:        {CONFIG_DIR}/agent.log")
    print()
    print("  IMPORTANT: Install Termux:Boot from F-Droid,")
    print("  then open it once to enable boot trigger.")
    print()
    print("  To start now:  python rasova_agent.py")
    print("  To remove:     python rasova_agent.py --uninstall")


def install_autostart():
    """
    Drop a VBS launcher into the Windows Startup folder.
    Runs silently (no console window) every time the user logs in.
    No admin rights required.
    Also pip-installs required dependencies.
    """
    if not IS_WINDOWS:
        # Android/Termux path
        if "com.termux" in os.environ.get("PREFIX", "") or os.path.isdir("/data/data/com.termux"):
            install_autostart_termux()
            sys.exit(0)
        print("Auto-start via Startup folder is Windows-only.")
        print("On Mac: add to Login Items in System Settings.")
        print("On Android (Termux): python rasova_agent.py --install-termux")
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
    """Remove auto-start launcher (Windows VBS or Termux boot script)."""
    if IS_WINDOWS:
        vbs_file = _vbs_path()
        if os.path.exists(vbs_file):
            os.remove(vbs_file)
            print("  Auto-start removed. Rasova Agent will no longer start at login.")
        else:
            print("  Auto-start was not installed (nothing to remove).")
    else:
        boot_script = os.path.expanduser("~/.termux/boot/rasova_agent.sh")
        if os.path.exists(boot_script):
            os.remove(boot_script)
            print("  Termux auto-start removed.")
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
        origins={"https://rasova.net", "https://*.rasova.net"},
        ping_interval=30,
        ping_timeout=10,
    ):
        await asyncio.Future()


# ── Polling mode (Android / low-power devices) ───────────────────────────────

def run_poll_mode(poll_url: str, interval: float = 2.0):  # noqa: C901
    """
    Polling mode — no WebSocket server, no open port, no firewall config.

    The agent is now a simple HTTP client:
      1. GET  <poll_url>jobs/          → list of pending print jobs
      2. Print each job to the local thermal printer
      3. POST <poll_url>done/<id>/     → mark job done
      4. Sleep `interval` seconds and repeat forever

    Android battery optimizer cannot kill outbound HTTP calls the same way
    it kills a WebSocket server.  Even if the process IS killed, it will
    be restarted by the Termux:Boot watchdog and pick up any queued jobs.

    Usage:
        python rasova_agent.py --poll https://your-site.com/orders/agent/<key>/
    """
    import base64
    import urllib.request as _req
    import urllib.error   as _err

    base = poll_url.rstrip("/") + "/"
    jobs_url = base + "jobs/"
    done_url  = base + "done/"
    fail_url  = base + "failed/"

    logger.info("=" * 56)
    logger.info("  Rasova Print Agent v%s — POLL MODE", VERSION)
    logger.info("  Polling: %s", jobs_url)
    logger.info("  Interval: %.1f s", interval)
    logger.info("  Platform: %s", sys.platform)
    logger.info("  Log: %s", LOG_PATH)
    logger.info("=" * 56)

    def _http_get(url):
        r = _req.urlopen(_req.Request(url, headers={"User-Agent": f"RasovaAgent/{VERSION}"}), timeout=15)
        return json.loads(r.read().decode())

    def _http_post(url, body=None):
        data = json.dumps(body or {}).encode()
        r = _req.urlopen(_req.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": f"RasovaAgent/{VERSION}"},
            method="POST",
        ), timeout=15)
        return json.loads(r.read().decode())

    consecutive_errors = 0

    while True:
        try:
            result  = _http_get(jobs_url)
            jobs    = result.get("jobs", [])
            consecutive_errors = 0

            for job in jobs:
                job_id   = job["id"]
                net_host = job.get("network_host", "")
                net_port = int(job.get("network_port", 9100))
                data_b64 = job.get("data_b64", "")

                if not data_b64 or not net_host:
                    _http_post(fail_url + f"{job_id}/", {"error": "empty job payload"})
                    continue

                data    = base64.b64decode(data_b64)
                success, message = print_raw_network(net_host, net_port, data)

                if success:
                    logger.info("Job #%d printed → %s:%d (%d bytes)", job_id, net_host, net_port, len(data))
                    _http_post(done_url + f"{job_id}/")
                else:
                    logger.warning("Job #%d failed: %s", job_id, message)
                    _http_post(fail_url + f"{job_id}/", {"error": message})

        except _err.HTTPError as e:
            if e.code == 403:
                logger.error("Invalid agent key — check the URL and regenerate key in Rasova settings")
                sys.exit(1)
            logger.warning("HTTP %d from server — will retry", e.code)
            consecutive_errors += 1
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors == 1:
                logger.warning("Poll error: %s", e)
            elif consecutive_errors % 30 == 0:
                logger.warning("Still failing after %d attempts: %s", consecutive_errors, e)

        time.sleep(interval)


def install_autostart_termux_poll(poll_url: str):
    """
    Install a Termux:Boot watchdog that auto-starts polling mode on boot.
    The watchdog loop means Android killing the process just restarts it in 3 s.
    """
    boot_dir = os.path.expanduser("~/.termux/boot")
    os.makedirs(boot_dir, exist_ok=True)

    agent_path = os.path.abspath(__file__)
    boot_script = os.path.join(boot_dir, "rasova_agent.sh")
    log_path    = os.path.join(CONFIG_DIR, "agent.log")

    script = (
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        "# Rasova Agent — auto-generated boot script\n"
        "# termux-wake-lock prevents Android from pausing the CPU\n"
        "termux-wake-lock\n"
        f"POLL_URL='{poll_url}'\n"
        "while true; do\n"
        f"    python {agent_path} --poll \"$POLL_URL\" >> {log_path} 2>&1\n"
        "    sleep 3\n"
        "done &\n"
    )

    with open(boot_script, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(boot_script, 0o755)

    print()
    print("  ✓ Rasova Agent will start automatically on boot")
    print(f"  Boot script: {boot_script}")
    print(f"  Logs:        {log_path}")
    print()
    print("  IMPORTANT — one-time Android setup (do this once):")
    print("  1. Install Termux:Boot from F-Droid, then open it once")
    print("  2. Android Settings → Apps → Termux → Battery → Unrestricted")
    print()
    print("  To start printing NOW (no reboot needed):")
    print(f"  python {agent_path} --poll '{poll_url}'")


if __name__ == "__main__":
    # ── CLI flags ─────────────────────────────────────────────────────────────
    if "--install" in sys.argv:
        install_autostart()
        sys.exit(0)

    if "--install-termux" in sys.argv:
        install_autostart_termux()
        sys.exit(0)

    if "--uninstall" in sys.argv:
        uninstall_autostart()
        sys.exit(0)

    # ── Poll mode — Android / low-power device ────────────────────────────────
    if "--poll" in sys.argv:
        idx = sys.argv.index("--poll")
        if idx + 1 >= len(sys.argv):
            print("Usage: python rasova_agent.py --poll https://your-site.com/orders/agent/<key>/")
            sys.exit(1)
        poll_url = sys.argv[idx + 1]

        # --install-boot also sets up the Termux:Boot watchdog
        if "--install-boot" in sys.argv:
            install_autostart_termux_poll(poll_url)
        else:
            _agent_config = _load_config()
            run_poll_mode(poll_url)
        sys.exit(0)

    # ── Normal run (WebSocket server — desktop) ───────────────────────────────
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
