#!/usr/bin/env python3
"""
iPhone Touchpad: the Mac-side server.

Standard library only. HTTP and WebSocket on one port; mouse and keyboard events
are posted through CoreGraphics (ctypes). No pip install needed.

Usage:  python3 server.py [--open] [--prompt] [--strict]
  --open    open the QR code / PIN page in the Mac's browser
  --prompt  show the macOS Accessibility permission prompt if access is missing
  --strict  use exactly the configured port (no fallback to the next free one)

Environment:
  TOUCHPAD_PORT  port to listen on (default 8899)
  TOUCHPAD_LANG  console language, "en" or "ru" (default: from LANG)
"""
import asyncio
import base64
import ctypes
import ctypes.util
import hashlib
import json
import math
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "darwin":
    sys.exit("iPhone Touchpad works on macOS only (it posts events through CoreGraphics).")

HERE = Path(__file__).resolve().parent
PORT_START = int(os.environ.get("TOUCHPAD_PORT", "8899"))
PORT = PORT_START
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PIN_FILE = HERE / ".pin"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
CLIENTS = set()  # phones that signed in with the PIN right now


def local_request(peer, headers):
    """True only for requests from this Mac that also name a local host.
    The Host check blocks DNS-rebinding: a web page cannot read /qr or /status
    through a hostname that merely resolves to 127.0.0.1."""
    host = headers.get("host", "").lower()
    if not host.endswith("]"):
        host = host.rsplit(":", 1)[0]
    return peer in LOOPBACK and host in LOCAL_HOSTS
# Icons from the assets folder (only this list is served, nothing else on disk)
ASSETS = {
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/favicon-32.png": ("favicon-32.png", "image/png"),
    "/favicon.ico": ("favicon-32.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
APP_NAME = {"en": "Touchpad", "ru": "Тачпад"}


def _console_lang():
    v = (os.environ.get("TOUCHPAD_LANG") or os.environ.get("LC_ALL")
         or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")
    return "ru" if v.lower().startswith("ru") else "en"


LANG = _console_lang()
MSG = {  # key: (English, Russian)
    "started": ("iPhone Touchpad is running", "iPhone-тачпад запущен"),
    "addr": ("Address for iPhone:    http://{ip}:{port}/", "Адрес для iPhone:      http://{ip}:{port}/"),
    "byname": ("Or by name:            http://{host}:{port}/", "Или по имени:          http://{host}:{port}/"),
    "pin": ("PIN:                   {pin}", "PIN:                   {pin}"),
    "qr": ("QR code on this Mac:   http://localhost:{port}/qr", "QR-код на этом Mac:    http://localhost:{port}/qr"),
    "noaccess1": ("Accessibility access is missing: the cursor will not move.",
                  "Нет доступа к Универсальному доступу: курсор двигаться не будет."),
    "noaccess2": ("System Settings > Privacy & Security > Accessibility: enable the app that runs the server",
                  "Системные настройки > Конфиденциальность и безопасность > Универсальный доступ: включи приложение,"),
    "noaccess3": ("(Terminal, or Python for autostart), then restart the server.",
                  "из которого запущен сервер (Терминал или Python при автозапуске), и перезапусти сервер."),
    "stop": ("Stop: Ctrl+C", "Остановить: Ctrl+C"),
    "nofree": ("No free port in {a}-{b}", "Нет свободного порта {a}–{b}"),
    "conn": ("[+] {peer} connected", "[+] {peer} подключился"),
    "authok": ("[ok] {peer} signed in with the PIN", "[ok] {peer} вошёл по PIN"),
    "bad": ("[!] {peer} wrong PIN (attempt {n})", "[!] {peer} неверный PIN (попытка {n})"),
    "disc": ("[-] {peer} disconnected", "[-] {peer} отключился"),
}


def m(key, **kw):
    return MSG[key][LANG == "ru"].format(**kw)


def pick_lang(accept_language):
    """'ru' if the browser's first preferred language is Russian, otherwise 'en'."""
    return "ru" if (accept_language or "").split(",")[0].strip().lower().startswith("ru") else "en"


# ───────────────────────── PIN ─────────────────────────
def load_pin() -> str:
    try:
        p = PIN_FILE.read_text().strip()
        if len(p) == 6 and p.isdigit():
            return p
    except OSError:
        pass
    p = "".join(secrets.choice("0123456789") for _ in range(6))
    PIN_FILE.write_text(p + "\n")
    os.chmod(PIN_FILE, 0o600)
    return p


PIN = load_pin()


# ───────────────────────── CoreGraphics ─────────────────────────
class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


FW = "/System/Library/Frameworks/"
CG = ctypes.CDLL(FW + "CoreGraphics.framework/CoreGraphics")
CF = ctypes.CDLL(FW + "CoreFoundation.framework/CoreFoundation")
AS = ctypes.CDLL(FW + "ApplicationServices.framework/ApplicationServices")

VP = ctypes.c_void_p


def _sig(fn, res, *args):
    fn.restype = res
    fn.argtypes = list(args)


_sig(CG.CGEventCreate, VP, VP)
_sig(CG.CGEventGetLocation, CGPoint, VP)
_sig(CG.CGEventCreateMouseEvent, VP, VP, ctypes.c_uint32, CGPoint, ctypes.c_uint32)
_sig(CG.CGEventPost, None, ctypes.c_uint32, VP)
_sig(CG.CGEventSetIntegerValueField, None, VP, ctypes.c_uint32, ctypes.c_int64)
_sig(CG.CGEventSetFlags, None, VP, ctypes.c_uint64)
_sig(CG.CGEventCreateKeyboardEvent, VP, VP, ctypes.c_uint16, ctypes.c_bool)
_sig(CG.CGEventKeyboardSetUnicodeString, None, VP, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16))
_sig(CG.CGEventCreateScrollWheelEvent2, VP, VP, ctypes.c_uint32, ctypes.c_uint32,
     ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)
_sig(CG.CGGetActiveDisplayList, ctypes.c_int32, ctypes.c_uint32,
     ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32))
_sig(CG.CGDisplayBounds, CGRect, ctypes.c_uint32)
_sig(CF.CFRelease, None, VP)
_sig(AS.AXIsProcessTrusted, ctypes.c_bool)


def ax_trusted(prompt: bool = False) -> bool:
    """Есть ли у процесса доступ к Универсальному доступу (нужен для событий мыши)."""
    if not prompt:
        return bool(AS.AXIsProcessTrusted())
    try:
        key = VP.in_dll(AS, "kAXTrustedCheckOptionPrompt")
        val = VP.in_dll(CF, "kCFBooleanTrue")
        kcb = ctypes.addressof(ctypes.c_char.in_dll(CF, "kCFTypeDictionaryKeyCallBacks"))
        vcb = ctypes.addressof(ctypes.c_char.in_dll(CF, "kCFTypeDictionaryValueCallBacks"))
        _sig(CF.CFDictionaryCreate, VP, VP, ctypes.POINTER(VP), ctypes.POINTER(VP),
             ctypes.c_long, VP, VP)
        keys = (VP * 1)(key.value)
        vals = (VP * 1)(val.value)
        d = CF.CFDictionaryCreate(None, keys, vals, 1, kcb, vcb)
        _sig(AS.AXIsProcessTrustedWithOptions, ctypes.c_bool, VP)
        res = bool(AS.AXIsProcessTrustedWithOptions(d))
        CF.CFRelease(d)
        return res
    except Exception:
        return bool(AS.AXIsProcessTrusted())


def post(ev):
    if ev:
        CG.CGEventPost(0, ev)  # kCGHIDEventTap
        CF.CFRelease(ev)


def display_bounds():
    ids = (ctypes.c_uint32 * 16)()
    n = ctypes.c_uint32(0)
    if CG.CGGetActiveDisplayList(16, ids, ctypes.byref(n)) != 0 or n.value == 0:
        return (0.0, 0.0, 1440.0, 900.0)
    x0 = y0 = 1e9
    x1 = y1 = -1e9
    for i in range(n.value):
        r = CG.CGDisplayBounds(ids[i])
        x0 = min(x0, r.origin.x)
        y0 = min(y0, r.origin.y)
        x1 = max(x1, r.origin.x + r.size.width)
        y1 = max(y1, r.origin.y + r.size.height)
    return (x0, y0, x1, y1)


class Mouse:
    def __init__(self):
        self.x, self.y = self.sys_pos()
        self.held = {"left": False, "right": False}
        self.count = 0
        self.ct = -10.0
        self.cx = self.cy = 0.0
        self.rx = self.ry = 0.0
        self._bounds = display_bounds()
        self._bounds_t = time.monotonic()

    @staticmethod
    def sys_pos():
        ev = CG.CGEventCreate(None)
        p = CG.CGEventGetLocation(ev)
        CF.CFRelease(ev)
        return p.x, p.y

    def bounds(self):
        if time.monotonic() - self._bounds_t > 3:
            self._bounds = display_bounds()
            self._bounds_t = time.monotonic()
        return self._bounds

    def sync(self):
        # If something else moved the cursor (a physical mouse), adopt its position.
        sx, sy = self.sys_pos()
        if abs(sx - self.x) > 3 or abs(sy - self.y) > 3:
            self.x, self.y = sx, sy

    def move(self, dx, dy):
        self.sync()
        x0, y0, x1, y1 = self.bounds()
        self.x = min(max(self.x + dx, x0), x1 - 1)
        self.y = min(max(self.y + dy, y0), y1 - 1)
        if self.held["left"]:
            t, b = 6, 0   # LeftMouseDragged
        elif self.held["right"]:
            t, b = 7, 1   # RightMouseDragged
        else:
            t, b = 5, 0   # MouseMoved
        ev = CG.CGEventCreateMouseEvent(None, t, CGPoint(self.x, self.y), b)
        CG.CGEventSetIntegerValueField(ev, 4, int(round(dx)))  # deltaX
        CG.CGEventSetIntegerValueField(ev, 5, int(round(dy)))  # deltaY
        post(ev)

    def button(self, name, down):
        if name not in self.held or self.held[name] == down:
            return
        self.sync()
        if name == "left":
            if down:
                now = time.monotonic()
                near = math.hypot(self.x - self.cx, self.y - self.cy) < 10
                self.count = min(self.count + 1, 3) if (now - self.ct < 0.45 and near) else 1
                self.ct, self.cx, self.cy = now, self.x, self.y
            t, b, count = (1 if down else 2), 0, self.count
        else:
            t, b, count = (3 if down else 4), 1, 1
        ev = CG.CGEventCreateMouseEvent(None, t, CGPoint(self.x, self.y), b)
        CG.CGEventSetIntegerValueField(ev, 1, count)  # clickState
        post(ev)
        self.held[name] = down

    def release_all(self):
        for name in ("left", "right"):
            self.button(name, False)

    def scroll(self, dx, dy):
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)
        if not ix and not iy:
            return
        self.rx -= ix
        self.ry -= iy
        ix = max(-2000, min(2000, ix))
        iy = max(-2000, min(2000, iy))
        post(CG.CGEventCreateScrollWheelEvent2(None, 0, 2, iy, ix, 0))  # 0 = pixel units


KEYCODES = {
    "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "delete": 117,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "a": 0, "c": 8, "v": 9, "x": 7, "z": 6, "w": 13, "t": 17, "r": 15, "f": 3,
}
ARROWS = {"left", "right", "down", "up"}
MODS = {"cmd": 0x100000, "shift": 0x20000, "alt": 0x80000, "ctrl": 0x40000}


def press_key(name, mods=()):
    code = KEYCODES.get(name)
    if code is None:
        return
    flags = 0
    for m in mods:
        flags |= MODS.get(m, 0)
    if name in ARROWS:
        flags |= 0x800000 | 0x200000  # fn + numpad flags, like real arrow keys
    for down in (True, False):
        ev = CG.CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            CG.CGEventSetFlags(ev, flags)
        post(ev)


def type_text(s):
    run = []

    def flush():
        if not run:
            return
        data = "".join(run).encode("utf-16-le")
        units = struct.unpack("<%dH" % (len(data) // 2), data)
        for i in range(0, len(units), 16):
            chunk = units[i:i + 16]
            buf = (ctypes.c_uint16 * len(chunk))(*chunk)
            for down in (True, False):
                ev = CG.CGEventCreateKeyboardEvent(None, 0, down)
                CG.CGEventSetFlags(ev, 0)
                CG.CGEventKeyboardSetUnicodeString(ev, len(chunk), buf)
                post(ev)
        run.clear()

    for ch in s:
        if ch == "\n":
            flush()
            press_key("return")
        elif ch == "\t":
            flush()
            press_key("tab")
        else:
            run.append(ch)
    flush()


# ───────────────────────── WebSocket ─────────────────────────
async def read_frame(reader):
    msg = bytearray()
    first_op = 1
    while True:
        b1, b2 = await reader.readexactly(2)
        fin, op, masked, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", await reader.readexactly(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", await reader.readexactly(8))[0]
        if ln > 65536:
            raise ValueError("frame too large")
        mask = await reader.readexactly(4) if masked else b""
        data = bytearray(await reader.readexactly(ln))
        if masked:
            for i in range(ln):
                data[i] ^= mask[i & 3]
        if op >= 8:
            return op, bytes(data)
        if op != 0:
            first_op = op
        msg += data
        if fin:
            return first_op, bytes(msg)


def ws_write(writer, op, data: bytes):
    n = len(data)
    if n < 126:
        head = bytes([0x80 | op, n])
    elif n < 65536:
        head = bytes([0x80 | op, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x80 | op, 127]) + struct.pack(">Q", n)
    writer.write(head + data)


def ws_send(writer, obj):
    ws_write(writer, 1, json.dumps(obj, separators=(",", ":")).encode())


mouse = None
auth_fails = 0
auth_locked_until = 0.0


def num(v, lim=5000.0):
    v = float(v)
    if not math.isfinite(v):
        return 0.0
    return max(-lim, min(lim, v))


async def ws_session(reader, writer, key, peer):
    global auth_fails, auth_locked_until
    accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest())
    writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    sock = writer.get_extra_info("socket")
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    authed = False
    print(m("conn", peer=peer))
    try:
        while True:
            op, payload = await asyncio.wait_for(read_frame(reader), 30)
            if op == 8:
                break
            if op == 9:
                ws_write(writer, 10, payload)
                continue
            if op != 1:
                continue
            try:
                msg = json.loads(payload)
                t = msg.get("t")
            except (ValueError, AttributeError):
                continue
            if t == "auth":
                now = time.monotonic()
                if now < auth_locked_until:
                    ws_send(writer, {"t": "auth", "ok": False, "wait": int(auth_locked_until - now) + 1})
                    continue
                if secrets.compare_digest(str(msg.get("pin", "")), PIN):
                    authed = True
                    CLIENTS.add(writer)
                    auth_fails = 0
                    ws_send(writer, {"t": "auth", "ok": True})
                    print(m("authok", peer=peer))
                else:
                    auth_fails += 1
                    wait = 0
                    if auth_fails >= 5:
                        wait = min(600, 15 * 2 ** (auth_fails - 5))
                        auth_locked_until = now + wait
                    print(m("bad", peer=peer, n=auth_fails))
                    ws_send(writer, {"t": "auth", "ok": False, "wait": wait})
                continue
            if t == "ping":
                ws_send(writer, {"t": "pong"})
                continue
            if not authed:
                ws_send(writer, {"t": "auth", "ok": False, "wait": 0})
                continue
            try:
                if t == "move":
                    mouse.move(num(msg["dx"], 3000), num(msg["dy"], 3000))
                elif t == "scroll":
                    mouse.scroll(num(msg.get("dx", 0)), num(msg.get("dy", 0)))
                elif t in ("down", "up"):
                    mouse.button(msg.get("b", "left"), t == "down")
                elif t == "click":
                    b = msg.get("b", "left")
                    mouse.button(b, True)
                    mouse.button(b, False)
                elif t == "text":
                    type_text(str(msg.get("s", ""))[:500])
                elif t == "key":
                    press_key(str(msg.get("k", "")), [str(m) for m in msg.get("m", [])][:4])
            except (KeyError, ValueError, TypeError):
                pass
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError):
        pass
    finally:
        CLIENTS.discard(writer)
        mouse.release_all()
        print(m("disc", peer=peer))


# ───────────────────────── HTTP ─────────────────────────
def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def http(writer, status, body, ctype="text/html; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
        f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body
    )


async def handle(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = head.decode("latin-1").split("\r\n")
        _, target, _ = lines[0].split(" ", 2)
        h = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                h[k.strip().lower()] = v.strip()
        path = target.split("?", 1)[0]
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        if path == "/ws" and h.get("upgrade", "").lower() == "websocket" and "sec-websocket-key" in h:
            await ws_session(reader, writer, h["sec-websocket-key"], peer)
        elif path == "/":
            lang = pick_lang(h.get("accept-language"))
            page = ((HERE / "index.html").read_text()
                    .replace("__LANG__", lang).replace("__APP_NAME__", APP_NAME[lang]))
            http(writer, "200 OK", page)
        elif path == "/manifest.webmanifest":
            name = APP_NAME[pick_lang(h.get("accept-language"))]
            manifest = {
                "name": name, "short_name": name, "start_url": "/", "display": "standalone",
                "background_color": "#000000", "theme_color": "#000000",
                "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
            }
            http(writer, "200 OK", json.dumps(manifest, ensure_ascii=False),
                 "application/manifest+json; charset=utf-8")
        elif path == "/status" and local_request(peer, h):
            body = {"pin": PIN, "port": PORT, "host": socket.gethostname(),
                    "urls": [f"http://{ip}:{PORT}/" for ip in lan_ips()],
                    "clients": len(CLIENTS), "trusted": ax_trusted()}
            http(writer, "200 OK", json.dumps(body), "application/json")
        elif path == "/qr" and local_request(peer, h):
            ips = lan_ips()
            url = f"http://{ips[0] if ips else 'localhost'}:{PORT}/"
            page = ((HERE / "qr.html").read_text().replace("__URL__", url).replace("__PIN__", PIN)
                    .replace("__LANG__", pick_lang(h.get("accept-language"))))
            http(writer, "200 OK", page)
        elif path in ASSETS:
            name, ctype = ASSETS[path]
            try:
                http(writer, "200 OK", (HERE / "assets" / name).read_bytes(), ctype)
            except OSError:
                http(writer, "404 Not Found", "not found", "text/plain; charset=utf-8")
        else:
            http(writer, "404 Not Found", "not found", "text/plain; charset=utf-8")
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global PORT, mouse
    mouse = Mouse()
    server = None
    # --strict (autostart): only the configured port, so the address on the phone never changes
    for p in range(PORT_START, PORT_START + (1 if "--strict" in sys.argv else 10)):
        try:
            server = await asyncio.start_server(handle, "0.0.0.0", p)
            PORT = p
            break
        except OSError:
            continue
    if server is None:
        print(m("nofree", a=PORT_START, b=PORT_START + (0 if "--strict" in sys.argv else 9)))
        return
    trusted = ax_trusted(prompt="--prompt" in sys.argv)
    ips = lan_ips()
    print(f"\n  {m('started')}\n")
    for ip in ips:
        print("  " + m("addr", ip=ip, port=PORT))
    print("  " + m("byname", host=socket.gethostname(), port=PORT))
    print("  " + m("pin", pin=PIN))
    print("  " + m("qr", port=PORT))
    if not trusted:
        print(f"\n  ! {m('noaccess1')}")
        print(f"    {m('noaccess2')}")
        print(f"    {m('noaccess3')}")
    print(f"\n  {m('stop')}\n", flush=True)
    if "--open" in sys.argv:
        subprocess.Popen(["open", f"http://localhost:{PORT}/qr"])
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
