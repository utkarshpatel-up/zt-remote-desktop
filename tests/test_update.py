#!/usr/bin/env python3
"""Update notice: the app reads latest.json and tells the phone on connect.

    python tests/test_update.py
"""
import asyncio
import json
import sys

import aiohttp
from aiohttp import web

from harness import Agent


async def main():
    feed = {"version": "9.0.0", "platforms": {}}
    app = web.Application()

    async def latest(req):
        return web.json_response(feed)
    app.router.add_get("/latest.json", latest)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18092).start()
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{name}: {got}" + ("" if good else f" (want {want})"))

    async def auth_ok(agent):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(agent.base + "/ws") as ws:
                await ws.send_json({"type": "auth", "token": "t0ken!"})
                return json.loads((await ws.receive()).data)

    newer = Agent(18769, update_url="http://127.0.0.1:18092/latest.json", token="t0ken!")
    unreachable = Agent(18770, token="t0ken!")
    try:
        version = (await newer.ready())["version"]
        await unreachable.ready()
        await asyncio.sleep(1)   # first background check
        m = await auth_ok(newer)
        check("auth_ok carries update", (m.get("update") or {}).get("version"), "9.0.0")
        check("auth_ok carries version", m.get("version"), version)
        m = await auth_ok(unreachable)
        check("no feed: no update", m.get("update"), None)
    finally:
        newer.stop()
        unreachable.stop()
        await runner.cleanup()
    print("ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
