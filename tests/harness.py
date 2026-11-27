"""Start the real app binary in headless demo mode for black-box tests.

The binary is $ZT_BIN, else src-tauri/target/{debug,release}/zt-remote-desktop.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

import aiohttp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def binary():
    if os.environ.get("ZT_BIN"):
        return os.environ["ZT_BIN"]
    exe = "zt-remote-desktop" + (".exe" if sys.platform.startswith("win") else "")
    for kind in ("debug", "release"):
        p = os.path.join(ROOT, "src-tauri", "target", kind, exe)
        if os.path.exists(p):
            return p
    raise SystemExit("build the app first (cargo build in src-tauri) or set ZT_BIN")


class Agent:
    def __init__(self, port, av_server=None, update_url=None, still=False, token=None):
        self.port, self.base = port, f"http://127.0.0.1:{port}"
        self.dir = tempfile.mkdtemp(prefix="zt-test-")
        if token:
            with open(os.path.join(self.dir, "zt-remote-config.json"), "w") as f:
                json.dump({"token": token}, f)
        args = [binary(), "--headless", "--demo", "--bind", "127.0.0.1",
                "--port", str(port), "--config-dir", self.dir]
        if still:
            args.append("--demo-still")
        if av_server is not None:
            args += ["--av-server", av_server]
        env = dict(os.environ, ZT_UPDATE_URL=update_url or "http://127.0.0.1:1/none")
        self.proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL)

    @property
    def token(self):
        with open(os.path.join(self.dir, "zt-remote-config.json")) as f:
            return json.load(f)["token"]

    async def ready(self):
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(self.base + "/health") as r:
                        if r.status == 200:
                            return await r.json()
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.1)
        raise RuntimeError("agent did not start")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)
