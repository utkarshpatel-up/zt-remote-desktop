#!/usr/bin/env python3
"""
ZT Remote Desktop - PC/Mac agent.

Streams the screen over a WebSocket and accepts mouse/keyboard input from
the bundled web client. Designed to run on a ZeroTier network: by default it
binds ONLY to the ZeroTier interface address, so it is never reachable from
the public internet or the local LAN. Every connection must present the
access token from the config file.

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
import secrets
import sys
import threading
import time

from aiohttp import web, WSMsgType

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "zt-remote-config.json")
CLIENT_HTML = os.path.join(HERE, "client.html")
DEFAULT_PORT = 8765


# --------------------------------------------------------------------------
# Config / token
# --------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("token") and cfg.get("port"):
                return cfg
        except Exception:
            pass
    cfg = {"token": secrets.token_urlsafe(24), "port": DEFAULT_PORT}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg


# --------------------------------------------------------------------------
# ZeroTier interface detection
# --------------------------------------------------------------------------

def find_zerotier_ip():
    """Return the IPv4 address of the ZeroTier interface, or None."""
    try:
        import psutil
    except Exception:
        return None
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if "zerotier" not in name.lower():
                continue
            for a in addrs:
                # AF_INET == 2
                if a.family == 2 and a.address and not a.address.startswith("169.254"):
                    return a.address
    except Exception:
        return None
    return None


def resolve_bind(requested):
    """Decide which local address to listen on."""
    if requested and requested != "auto":
        return "0.0.0.0" if requested == "all" else requested
    zt_ip = find_zerotier_ip()
    if zt_ip:
        return zt_ip
    print("WARNING: No ZeroTier interface found. Falling back to 0.0.0.0 "
          "(reachable on your LAN). The access token is still required, "
          "but prefer running with ZeroTier connected.")
    return "0.0.0.0"


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

    def frame_jpeg(self, quality, max_width):
        from PIL import Image
        sct = self._sct()
        mon = sct.monitors[1]
        raw = sct.grab(mon)
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

    def frame_jpeg(self, quality, max_width):
        from PIL import Image, ImageDraw
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
    "capslock": "caps_lock",
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
            return getattr(self._kb_cls.Key, SPECIAL_KEYS[name])
        if len(name) == 1:
            return name
        return None

    def move(self, x, y):
        self._mouse.position = (int(x), int(y))

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

def build_app(cfg, demo=False):
    capture = DemoCapture() if demo else RealCapture()
    controller = DemoInput(capture) if demo else RealInput()
    token = cfg["token"]

    app = web.Application()

    async def index(request):
        return web.FileResponse(CLIENT_HTML)

    async def health(request):
        return web.json_response({"ok": True, "demo": demo})

    async def ws_handler(request):
        ws = web.WebSocketResponse(max_msg_size=1 << 20)
        await ws.prepare(request)

        # --- auth: first message must carry the token -------------------
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws
        authed = False
        if first.type == WSMsgType.TEXT:
            try:
                msg = json.loads(first.data)
                authed = msg.get("type") == "auth" and hmac.compare_digest(
                    str(msg.get("token", "")), token)
            except Exception:
                authed = False
        if not authed:
            await ws.send_json({"type": "auth_failed"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "demo": demo})

        settings = {"fps": 12, "quality": 60, "max_width": 1280}
        loop = asyncio.get_running_loop()

        async def stream():
            while not ws.closed:
                t0 = loop.time()
                try:
                    data, sw, sh = await loop.run_in_executor(
                        None, capture.frame_jpeg,
                        settings["quality"], settings["max_width"])
                    await ws.send_json({"type": "meta",
                                        "screen_w": sw, "screen_h": sh})
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
                await asyncio.sleep(max(0.0, 1.0 / max(1, settings["fps"]) - elapsed))

        stream_task = asyncio.ensure_future(stream())

        def scale(msg):
            return (float(msg.get("x", 0)) * capture.screen_w,
                    float(msg.get("y", 0)) * capture.screen_h)

        try:
            async for raw in ws:
                if raw.type != WSMsgType.TEXT:
                    continue
                try:
                    msg = json.loads(raw.data)
                except Exception:
                    continue
                kind = msg.get("type")
                if kind == "config":
                    for k in ("fps", "quality", "max_width"):
                        if k in msg:
                            settings[k] = msg[k]
                elif kind == "mouse_move":
                    x, y = scale(msg)
                    controller.move(x, y)
                elif kind == "mouse_down":
                    x, y = scale(msg)
                    controller.move(x, y)
                    controller.button(msg.get("button", "left"), True)
                elif kind == "mouse_up":
                    controller.button(msg.get("button", "left"), False)
                elif kind == "scroll":
                    controller.scroll(msg.get("dx", 0), msg.get("dy", 0))
                elif kind == "key":
                    controller.key_tap(msg.get("key", ""), msg.get("modifiers"))
                elif kind == "key_down":
                    controller.key_down(msg.get("key", ""))
                elif kind == "key_up":
                    controller.key_up(msg.get("key", ""))
                elif kind == "text":
                    controller.type_text(str(msg.get("text", "")))
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
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--bind", default="auto",
                    help="'auto' (ZeroTier interface only), 'all', or an IP")
    args = ap.parse_args()

    cfg = load_config()
    port = args.port or cfg["port"]
    bind = resolve_bind(args.bind)
    zt_ip = find_zerotier_ip()

    print("=" * 58)
    print("  ZT Remote Desktop agent")
    print(f"  Mode        : {'DEMO' if args.demo else 'LIVE'}")
    print(f"  Listening on: {bind}:{port}")
    if zt_ip:
        print(f"  Open on your phone: http://{zt_ip}:{port}")
    print(f"  Access token: {cfg['token']}")
    print("  (token is also saved in zt-remote-config.json - keep it private)")
    print("=" * 58)
    sys.stdout.flush()

    web.run_app(build_app(cfg, demo=args.demo), host=bind, port=port,
                print=None)


if __name__ == "__main__":
    main()
