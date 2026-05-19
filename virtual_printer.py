#!/usr/bin/env python3
"""
Rasova Virtual Thermal Printer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Listens on TCP 9100 — the ESC/POS standard port.
Set any Kitchen Station → Printer IP = 127.0.0.1 in Rasova setup.
Every print job appears in this terminal window.

Run:  python virtual_printer.py
Stop: Ctrl+C
"""

import socket
import datetime

HOST = "0.0.0.0"   # accept from localhost or LAN
PORT = 9100
W    = 48          # chars per line (matches 80mm paper)


def strip_escpos(data: bytes) -> str:
    """Remove ESC/POS control codes and return human-readable text."""
    out = []
    i   = 0
    while i < len(data):
        b = data[i]

        if b == 0x1B:                      # ESC — 1 or 2 extra bytes
            i += 1
            if i >= len(data): break
            cmd = data[i]
            if cmd in (0x40,):             # ESC @ init — no extra
                pass
            elif cmd in (0x21, 0x45, 0x61, 0x4D, 0x74):
                i += 1                     # 1 data byte
            elif cmd == 0x61:
                i += 1
            else:
                pass
            i += 1

        elif b == 0x1D:                    # GS — variable
            i += 1
            if i >= len(data): break
            cmd = data[i]
            if cmd == 0x56:               # GS V — paper cut
                out.append("\n" + "─" * W + "  ✂ CUT\n")
                i += 2
            elif cmd in (0x21, 0x42, 0x77, 0x68):
                i += 2
            elif cmd == 0x6B:             # GS k — barcode (variable)
                i += 2
                while i < len(data) and data[i] != 0x00:
                    i += 1
                i += 1
            else:
                i += 1

        elif b == 0x10:                    # DLE
            i += 3

        elif b == 0x0A:                    # LF
            out.append("\n")
            i += 1

        elif b == 0x0D:                    # CR — ignore
            i += 1

        elif b == 0x00:                    # NUL — ignore
            i += 1

        elif 0x20 <= b <= 0x7E:           # printable ASCII
            out.append(chr(b))
            i += 1

        elif b > 0x7E:                     # extended (₹ etc) — placeholder
            out.append("?")
            i += 1

        else:
            i += 1

    return "".join(out)


def run():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)

    print(f"""
╔{'═' * (W + 4)}╗
║  🖨  Rasova Virtual Thermal Printer{' ' * (W - 31)}║
║  Listening on 127.0.0.1:{PORT}   (Ctrl+C to stop){' ' * (W - 44)}║
╚{'═' * (W + 4)}╝

  → In Rasova: Setup → Kitchen Stations → enter Printer IP = 127.0.0.1
  → Trigger a print from billing screen or use:
      python manage.py preview_print --list
""")

    job_count = 0
    while True:
        try:
            conn, addr = server.accept()
        except KeyboardInterrupt:
            print("\n  Printer stopped.")
            break

        job_count += 1
        data = b""
        conn.settimeout(2.0)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        finally:
            conn.close()

        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        text = strip_escpos(data)

        print(f"\n╔{'═' * (W + 4)}╗")
        print(f"║  JOB #{job_count:03d}  ·  {ts}  ·  {len(data)} bytes{' ' * max(0, W - 26)}║")
        print(f"╠{'═' * (W + 4)}╣")
        for line in text.splitlines():
            print(f"║  {line:<{W + 2}}║")
        print(f"╚{'═' * (W + 4)}╝\n")


if __name__ == "__main__":
    run()
