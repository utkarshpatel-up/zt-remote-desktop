#!/usr/bin/env python3
"""
ZT Remote Desktop - PC/Mac agent.

Streams the screen over a WebSocket and accepts mouse/keyboard input from
the bundled web client. Designed to run on a ZeroTier network: by default it
binds ONLY to the ZeroTier interface address, so it is never reachable from
the public internet or the local LAN. Every connection must either present
the access password (the "token" in the config file) or sign in with an
AV Backup team account, checked live against the team's PocketBase server.

Works on Windows and macOS (and Linux for testing). Heavy dependencies
(mss, pynput, psutil, Pillow) are imported lazily so `--demo` mode can run
with only aiohttp + Pillow: demo mode streams an animated test pattern and
logs input events instead of touching a real screen/keyboard.
"""

import argparse
import asyncio
import hmac
import io
import json
import os
import re
import secrets
import sys
import threading
import time

from aiohttp import web, WSMsgType

if getattr(sys, "frozen", False):
    # standalone build (PyInstaller): config lives next to the .exe / binary,
    # client.html is unpacked with the program
    HERE = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = HERE
CONFIG_PATH = os.path.join(HERE, "zt-remote-config.json")
CLIENT_HTML = os.path.join(RES_DIR, "client.html")
DEFAULT_PORT = 8765
DEFAULT_ROLES = ["admin", "operator"]


# --------------------------------------------------------------------------
# Config / token
# --------------------------------------------------------------------------

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}
    before = dict(cfg)
    if not isinstance(cfg.get("token"), str) or not cfg["token"].strip():
        cfg["token"] = secrets.token_urlsafe(24)
    if not isinstance(cfg.get("port"), int):
        cfg["port"] = DEFAULT_PORT
    if not isinstance(cfg.get("av_backup_server"), str):
        cfg["av_backup_server"] = ""     # "" = use the AV Backup app's setting if installed
    if not isinstance(cfg.get("av_backup_roles"), list):
        cfg["av_backup_roles"] = list(DEFAULT_ROLES)
    if cfg != before:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


# --------------------------------------------------------------------------
# AV Backup team accounts (PocketBase on the office server, over ZeroTier)
# --------------------------------------------------------------------------

def _av_backup_config_dirs():
    """Where the AV Backup desktop app keeps sync-config.json (Electron userData)."""
    names = ["AV Backup", "av-backup"]
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [os.path.join(base, n) for n in names]


def normalize_server_url(url):
    """'10.7.0.5:8090' -> 'http://10.7.0.5:8090'. Returns '' when unusable."""
    from urllib.parse import urlsplit
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    if parts.username or parts.password or parts.query or parts.fragment:
        return ""
    return url


def find_av_backup_server(cfg):
    """Server from our config first, else from the AV Backup app on this PC."""
    url = normalize_server_url(cfg.get("av_backup_server"))
    if url:
        return url, "zt-remote-config.json"
    for d in _av_backup_config_dirs():
        try:
            with open(os.path.join(d, "sync-config.json"), "r", encoding="utf-8") as f:
                url = normalize_server_url(json.load(f).get("serverUrl"))
            if url:
                return url, "AV Backup app"
        except Exception:
            continue
    return "", ""


class AuthError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


async def verify_av_backup(server, email, password, roles, timeout=15):
    """Sign in to the AV Backup team server; returns the member's display name.

    Only active members (avb_members) with one of `roles` are let in, checked
    live on every login, so removing someone in the dashboard locks them out.
    The password is sent to the team server only, never stored or logged.
    """
    import aiohttp
    t = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=t) as s:
            async with s.post(f"{server}/api/collections/users/auth-with-password",
                              json={"identity": email, "password": password},
                              allow_redirects=False) as r:
                if r.status in (400, 401, 403, 404):
                    raise AuthError("wrong_credentials")
                if r.status != 200:
                    raise AuthError("server_error")
                body = await r.json(content_type=None)
            token = body.get("token") if isinstance(body, dict) else None
            if not isinstance(token, str) or not token:
                raise AuthError("server_error")
            async with s.post(f"{server}/api/avb/rpc/avb_me", json={},
                              headers={"Authorization": token},
                              allow_redirects=False) as r:
                if r.status in (401, 403):
                    raise AuthError("not_member")
                if r.status != 200:
                    raise AuthError("server_error")
                me = await r.json(content_type=None)
    except AuthError:
        raise
    except Exception:
        raise AuthError("server_unreachable")
    role = me.get("role") if isinstance(me, dict) else None
    if role not in roles:
        raise AuthError("role_not_allowed")
    return str(me.get("display_name") or email)


class LoginLimiter:
    """Slows down password guessing: 5 failures from one address -> 60 s wait."""

    def __init__(self, max_fails=5, window=300, lockout=60):
        self.max_fails, self.window, self.lockout = max_fails, window, lockout
        self.fails = {}      # ip -> [timestamps]
        self.locked = {}     # ip -> until

    def blocked(self, ip):
        return self.locked.get(ip, 0) > time.monotonic()

    def failed(self, ip):
        now = time.monotonic()
        recent = [t for t in self.fails.get(ip, []) if now - t < self.window]
        recent.append(now)
        self.fails[ip] = recent
        if len(recent) >= self.max_fails:
            self.locked[ip] = now + self.lockout
            self.fails[ip] = []

    def ok(self, ip):
        self.fails.pop(ip, None)


# --------------------------------------------------------------------------
# ZeroTier interface detection
# --------------------------------------------------------------------------

# ZeroTier names its adapter differently on every OS:
#   Windows: "ZeroTier One [<network id>]"
#   Linux:   "zt" + 8 letters/digits, e.g. "zt5u4y3kc6"
#   macOS:   "feth" + digits (ZeroTier 1.6 and later), e.g. "feth2873"
_ZT_IFACE = re.compile(r"^(zt[0-9a-z]{8}|feth\d+)$")


def is_zerotier_interface(name):
    n = str(name).lower()
    return "zerotier" in n or bool(_ZT_IFACE.match(n))


def find_zerotier_ips():
    """Return the IPv4 addresses of all ZeroTier interfaces (may be empty)."""
    try:
        import psutil
    except Exception:
        return []
    ips = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if not is_zerotier_interface(name):
                continue
            for a in addrs:
                # AF_INET == 2
                if (a.family == 2 and a.address and not a.address.startswith("169.254")
                        and a.address not in ips):
                    ips.append(a.address)
    except Exception:
        return []
    return ips


def find_zerotier_ip():
    """Return the first ZeroTier IPv4 address, or None."""
    ips = find_zerotier_ips()
    return ips[0] if ips else None


def resolve_bind(requested, wait=True, poll_seconds=3.0):
    """Decide which local address(es) to listen on.

    'auto' listens only on ZeroTier. If ZeroTier is not up yet (the agent was
    started at login before ZeroTier connected), it waits for it instead of
    opening the agent to the LAN. 'all' is the explicit opt-in for every
    interface.
    """
    if requested and requested != "auto":
        return ["0.0.0.0"] if requested == "all" else [requested]
    ips = find_zerotier_ips()
    if ips or not wait:
        return ips
    print("Waiting for ZeroTier to connect... (the agent listens only on "
          "ZeroTier; use --bind all to listen on every network instead)")
    sys.stdout.flush()
    last_note = time.monotonic()
    while not ips:
        time.sleep(poll_seconds)
        ips = find_zerotier_ips()
        if not ips and time.monotonic() - last_note >= 60:
            print("Still waiting for ZeroTier. Check the ZeroTier app shows "
                  "this computer as Connected.")
            sys.stdout.flush()
            last_note = time.monotonic()
    return ips


# --------------------------------------------------------------------------
# Screen capture
# --------------------------------------------------------------------------

class RealCapture:
    """Captures the primary display with mss and encodes JPEG frames."""

    def __init__(self):
        import mss  # noqa: F401  (fail fast with a clear error)
        self._local = threading.local()
        self.screen_w = 0
        self.screen_h = 0

    def _sct(self):
        if not hasattr(self._local, "sct"):
            import mss
            self._local.sct = mss.mss()
            mon = self._local.sct.monitors[1]
            self.screen_w, self.screen_h = mon["width"], mon["height"]
        return self._local.sct

    def frame_jpeg(self, quality, max_width, state=None):
        """Return (jpeg or None, screen_w, screen_h).

        `state` is a per-connection dict. When the screen and the settings are
        the same as the frame last sent on that connection, the JPEG is None
        and nothing is encoded or sent.
        """
        from PIL import Image
        sct = self._sct()
        mon = sct.monitors[1]
        raw = sct.grab(mon)
        if state is not None:
            key = (raw.size, quality, max_width)
            pixels = raw.bgra
            if state.get("key") == key and state.get("pixels") == pixels:
                return None, self.screen_w, self.screen_h
            state["key"], state["pixels"] = key, pixels
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        if max_width and img.width > max_width:
            h = int(img.height * max_width / img.width)
            img = img.resize((max_width, h))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=int(quality))
        return buf.getvalue(), self.screen_w, self.screen_h


class DemoCapture:
    """Animated test pattern so the whole pipeline can be tested anywhere."""

    def __init__(self):
        self.screen_w, self.screen_h = 1280, 720
        self._n = 0
        self.cursor = (640, 360)
        self.last_event = "demo ready"
        # --demo-still: freeze the picture so unchanged-frame skipping can be tested
        self.paused = False

    def frame_jpeg(self, quality, max_width, state=None):
        from PIL import Image, ImageDraw
        if self.paused and state is not None and state.get("sent"):
            return None, self.screen_w, self.screen_h
        if state is not None:
            state["sent"] = True
        self._n += 1
        img = Image.new("RGB", (960, 540), (24, 28, 38))
        d = ImageDraw.Draw(img)
        x = 60 + (self._n * 7) % 780
        d.rectangle([x, 200, x + 120, 320], fill=(64, 160, 255))
        d.rectangle([0, 0, 960, 46], fill=(16, 18, 24))
        d.text((14, 15), "ZT Remote Desktop - DEMO MODE", fill=(230, 235, 245))
        d.text((14, 470), f"frame {self._n}   last input: {self.last_event}",
               fill=(170, 180, 200))
        cx, cy = int(self.cursor[0] * 960 / self.screen_w), int(self.cursor[1] * 540 / self.screen_h)
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=(255, 210, 60), width=2)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=int(quality))
        return buf.getvalue(), self.screen_w, self.screen_h


# --------------------------------------------------------------------------
# Input injection
# --------------------------------------------------------------------------

SPECIAL_KEYS = {
    "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc",
    "tab": "tab", "backspace": "backspace", "delete": "delete",
    "del": "delete", "space": "space", "up": "up", "down": "down",
    "left": "left", "right": "right", "home": "home", "end": "end",
    "pageup": "page_up", "pagedown": "page_down",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "cmd": "cmd", "win": "cmd", "command": "cmd", "meta": "cmd",
    "capslock": "caps_lock", "insert": "insert",
    # names browsers use for KeyboardEvent.key
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
    "arrowright": "right", "os": "cmd",
}
for _i in range(1, 13):
    SPECIAL_KEYS[f"f{_i}"] = f"f{_i}"


class RealInput:
    def __init__(self):
        from pynput import mouse, keyboard
        self._mouse = mouse.Controller()
        self._kb = keyboard.Controller()
        self._mouse_btn = {"left": mouse.Button.left,
                           "right": mouse.Button.right,
                           "middle": mouse.Button.middle}
        self._kb_cls = keyboard

    def _key(self, name):
        name = str(name).lower()
        if name in SPECIAL_KEYS:
            # some keys (e.g. insert) do not exist on every OS
            return getattr(self._kb_cls.Key, SPECIAL_KEYS[name], None)
        if len(name) == 1:
            return name
        return None

    def move(self, x, y):
        self._mouse.position = (int(x), int(y))

    def position(self):
        x, y = self._mouse.position
        return float(x), float(y)

    def button(self, btn, down):
        b = self._mouse_btn.get(btn, self._mouse_btn["left"])
        if down:
            self._mouse.press(b)
        else:
            self._mouse.release(b)

    def scroll(self, dx, dy):
        self._mouse.scroll(int(dx), int(dy))

    def key_tap(self, key, modifiers=None):
        k = self._key(key)
        if k is None:
            return
        mods = [self._key(m) for m in (modifiers or [])]
        mods = [m for m in mods if m is not None]
        for m in mods:
            self._kb.press(m)
        try:
            self._kb.press(k)
            self._kb.release(k)
        finally:
            for m in reversed(mods):
                self._kb.release(m)

    def key_down(self, key):
        k = self._key(key)
        if k is not None:
            self._kb.press(k)

    def key_up(self, key):
        k = self._key(key)
        if k is not None:
            self._kb.release(k)

    def type_text(self, text):
        self._kb.type(text)


class DemoInput:
    def __init__(self, capture):
        self._cap = capture

    def _log(self, what):
        self._cap.last_event = what
        print(f"[demo input] {what}")

    def move(self, x, y):
        self._cap.cursor = (x, y)
        self._cap.last_event = f"move {int(x)},{int(y)}"

    def position(self):
        return float(self._cap.cursor[0]), float(self._cap.cursor[1])

    def button(self, btn, down):
        self._log(f"mouse {btn} {'down' if down else 'up'}")

    def scroll(self, dx, dy):
        self._log(f"scroll {dx},{dy}")

    def key_tap(self, key, modifiers=None):
        self._log(f"key {'+'.join(modifiers or [])}+{key}".strip("+"))

    def key_down(self, key):
        self._log(f"keydown {key}")

    def key_up(self, key):
        self._log(f"keyup {key}")

    def type_text(self, text):
        self._log(f"type {text!r}")


# --------------------------------------------------------------------------
# Web app
# --------------------------------------------------------------------------

PLATFORM = ("mac" if sys.platform == "darwin"
            else "windows" if sys.platform.startswith("win") else "linux")

# allowed ranges for the client's stream settings
SETTING_LIMITS = {"fps": (1, 30), "quality": (10, 95), "max_width": (0, 3840)}


def _num(value, default, lo, hi):
    """A finite number clamped to [lo, hi]; `default` when it is not one."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return min(hi, max(lo, v))


def build_app(cfg, demo=False, demo_still=False, av_server=""):
    capture = DemoCapture() if demo else RealCapture()
    if demo:
        capture.paused = demo_still
    controller = DemoInput(capture) if demo else RealInput()
    token = cfg["token"]
    roles = [r for r in cfg.get("av_backup_roles", DEFAULT_ROLES) if isinstance(r, str)]
    limiter = LoginLimiter()

    app = web.Application()

    async def index(request):
        return web.FileResponse(CLIENT_HTML)

    async def health(request):
        return web.json_response({"ok": True, "demo": demo,
                                  "av_backup": bool(av_server)})

    async def ws_handler(request):
        # heartbeat: notice a phone that dropped off ZeroTier even when the
        # screen is still and no frames are flowing
        ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=20)
        await ws.prepare(request)

        # --- auth: first message must carry the token -------------------
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws
        ip = request.remote or "?"
        who, reason = None, "wrong_password"
        if limiter.blocked(ip):
            reason = "too_many_attempts"
        elif first.type == WSMsgType.TEXT:
            try:
                msg = json.loads(first.data)
            except Exception:
                msg = None
            if isinstance(msg, dict) and msg.get("type") == "auth":
                if msg.get("method") == "av_backup":
                    email = str(msg.get("email", "")).strip()
                    password = str(msg.get("password", ""))
                    if not av_server:
                        reason = "av_backup_not_set_up"
                    elif not email or not password:
                        reason = "wrong_credentials"
                    else:
                        try:
                            who = await verify_av_backup(av_server, email, password, roles)
                        except AuthError as e:
                            reason = e.reason
                elif hmac.compare_digest(str(msg.get("token", "")).encode(),
                                         token.encode()):
                    who = "access password"
        if who is None:
            if reason in ("wrong_password", "wrong_credentials", "not_member",
                          "role_not_allowed"):
                limiter.failed(ip)
            print(f"[auth] refused {ip}: {reason}")
            sys.stdout.flush()
            await ws.send_json({"type": "auth_failed", "reason": reason})
            await ws.close()
            return ws
        limiter.ok(ip)
        print(f"[auth] {ip} connected ({who})")
        sys.stdout.flush()
        await ws.send_json({"type": "auth_ok", "demo": demo,
                            "platform": PLATFORM})

        settings = {"fps": 12, "quality": 60, "max_width": 1280}
        frame_state = {}   # this connection's last sent frame
        loop = asyncio.get_running_loop()

        def grab():
            data, sw, sh = capture.frame_jpeg(
                settings["quality"], settings["max_width"], frame_state)
            try:
                cx, cy = controller.position()
            except Exception:
                cx = cy = None
            return data, sw, sh, cx, cy

        async def stream():
            last_meta = None
            while not ws.closed:
                t0 = loop.time()
                try:
                    data, sw, sh, cx, cy = await loop.run_in_executor(None, grab)
                    meta = {"type": "meta", "screen_w": sw, "screen_h": sh}
                    if cx is not None and sw and sh:
                        meta["cx"] = round(min(1.0, max(0.0, cx / sw)), 4)
                        meta["cy"] = round(min(1.0, max(0.0, cy / sh)), 4)
                    # only send what changed: a still screen costs nothing
                    if meta != last_meta:
                        await ws.send_json(meta)
                        last_meta = meta
                    if data is not None:
                        await ws.send_bytes(data)
                except (ConnectionResetError, asyncio.CancelledError):
                    break
                except Exception as e:  # capture hiccup: report, keep going
                    try:
                        await ws.send_json({"type": "error", "message": str(e)})
                    except Exception:
                        break
                    await asyncio.sleep(0.5)
                elapsed = loop.time() - t0
                await asyncio.sleep(max(0.0, 1.0 / settings["fps"] - elapsed))

        stream_task = asyncio.ensure_future(stream())

        def scale(msg):
            return (_num(msg.get("x"), 0, 0, 1) * capture.screen_w,
                    _num(msg.get("y"), 0, 0, 1) * capture.screen_h)

        def handle(msg):
            kind = msg.get("type")
            if kind == "config":
                for k, (lo, hi) in SETTING_LIMITS.items():
                    if k in msg:
                        settings[k] = int(_num(msg[k], settings[k], lo, hi))
                if settings["max_width"] and settings["max_width"] < 320:
                    settings["max_width"] = 320   # 0 = original size
            elif kind == "mouse_move":
                controller.move(*scale(msg))
            elif kind == "mouse_move_rel":
                # trackpad mode: dx/dy are fractions of the screen size
                if capture.screen_w and capture.screen_h:
                    x, y = controller.position()
                    x += _num(msg.get("dx"), 0, -1, 1) * capture.screen_w
                    y += _num(msg.get("dy"), 0, -1, 1) * capture.screen_h
                    controller.move(min(capture.screen_w - 1, max(0, x)),
                                    min(capture.screen_h - 1, max(0, y)))
            elif kind == "mouse_down":
                # without x/y the button goes down where the cursor already is
                if "x" in msg and "y" in msg:
                    controller.move(*scale(msg))
                controller.button(msg.get("button", "left"), True)
            elif kind == "mouse_up":
                controller.button(msg.get("button", "left"), False)
            elif kind == "scroll":
                controller.scroll(_num(msg.get("dx"), 0, -50, 50),
                                  _num(msg.get("dy"), 0, -50, 50))
            elif kind == "key":
                controller.key_tap(msg.get("key", ""), msg.get("modifiers"))
            elif kind == "key_down":
                controller.key_down(msg.get("key", ""))
            elif kind == "key_up":
                controller.key_up(msg.get("key", ""))
            elif kind == "text":
                controller.type_text(str(msg.get("text", "")))

        try:
            async for raw in ws:
                if raw.type != WSMsgType.TEXT:
                    continue
                try:
                    msg = json.loads(raw.data)
                    if isinstance(msg, dict):
                        handle(msg)
                except Exception as e:  # one bad message must not end the session
                    print(f"input error: {e}")
        finally:
            stream_task.cancel()
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", ws_handler)
    return app


def main():
    ap = argparse.ArgumentParser(description="ZT Remote Desktop agent")
    ap.add_argument("--demo", action="store_true",
                    help="test pattern + logged input, no real screen control")
    ap.add_argument("--demo-still", action="store_true",
                    help="with --demo: a still picture (tests that unchanged "
                         "frames are not resent)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--av-server", default=None,
                    help="AV Backup team server address, e.g. http://10.7.0.5:8090 "
                         "(saved in the config; default: read from the AV Backup app)")
    ap.add_argument("--bind", default="auto",
                    help="'auto' (ZeroTier interface only), 'all', or an IP")
    args = ap.parse_args()

    cfg = load_config()
    if args.av_server is not None:
        url = normalize_server_url(args.av_server)
        if args.av_server.strip() and not url:
            sys.exit(f"Not a valid server address: {args.av_server}")
        cfg["av_backup_server"] = url
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    av_server, av_source = find_av_backup_server(cfg)
    port = args.port or cfg["port"]
    binds = resolve_bind(args.bind)
    zt_ips = find_zerotier_ips()

    print("=" * 58)
    print("  ZT Remote Desktop agent")
    print(f"  Mode        : {'DEMO' if args.demo else 'LIVE'}")
    print(f"  Listening on: {', '.join(f'{b}:{port}' for b in binds)}")
    for ip in zt_ips:
        print(f"  Open on your phone: http://{ip}:{port}")
    print(f"  Access password: {cfg['token']}")
    print("  (saved as \"token\" in zt-remote-config.json - keep it private)")
    if av_server:
        print(f"  AV Backup login: ON  ({av_server}, from {av_source})")
        print(f"    allowed roles: {', '.join(cfg.get('av_backup_roles', DEFAULT_ROLES))}")
    else:
        print("  AV Backup login: OFF (AV Backup not found on this PC; run with")
        print("    --av-server http://<team-server>:8090 to turn it on)")
    print("=" * 58)
    sys.stdout.flush()

    web.run_app(build_app(cfg, demo=args.demo, demo_still=args.demo_still,
                          av_server=av_server), host=binds, port=port,
                print=None)


if __name__ == "__main__":
    main()
