#!/usr/bin/env python3
"""Update-check tests against a fake GitHub releases API; no network needed.

    python test_update.py
"""
import asyncio
import json
import sys

import aiohttp
from aiohttp import web

import agent

ASSETS = [{"name": n, "browser_download_url": "https://dl/" + n} for n in (
    "ZT-Remote-Desktop-Setup-9.0.0.exe", "ZT-Remote-Desktop-Windows-Portable-9.0.0.zip",
    "ZT-Remote-Desktop-macOS-9.0.0.dmg")]
RELEASE = {"tag_name": "v9.0.0", "html_url": "https://rel/v9", "assets": ASSETS}


async def main():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{name}: {got}" + ("" if good else f" (want {want})"))

    check("parse v1.2.3", agent.parse_version("v1.2.3"), (1, 2, 3))
    check("parse 1.10", agent.parse_version("1.10"), (1, 10, 0))
    check("parse junk", agent.parse_version("latest"), None)
    check("1.10.0 > 1.9.9", agent.parse_version("1.10.0") > agent.parse_version("1.9.9"), True)
    check("installed Windows -> Setup", agent.pick_download(ASSETS, "windows", True), "https://dl/ZT-Remote-Desktop-Setup-9.0.0.exe")
    check("portable Windows -> zip", agent.pick_download(ASSETS, "windows", False), "https://dl/ZT-Remote-Desktop-Windows-Portable-9.0.0.zip")
    check("Mac -> dmg", agent.pick_download(ASSETS, "mac"), "https://dl/ZT-Remote-Desktop-macOS-9.0.0.dmg")

    release = dict(RELEASE)
    gh = web.Application()

    async def latest(req):
        return web.json_response(release)
    gh.router.add_get("/latest", latest)
    r = web.AppRunner(gh)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", 18092).start()
    api = "http://127.0.0.1:18092/latest"

    found = await agent.UpdateChecker(current="1.1.0", api=api).check_once()
    check("newer release found", found and found["version"], "9.0.0")
    check("same version: none", await agent.UpdateChecker(current="9.0.0", api=api).check_once(), None)
    check("newer local: none", await agent.UpdateChecker(current="9.1.0", api=api).check_once(), None)
    check("unreachable: none", await agent.UpdateChecker(api="http://127.0.0.1:1/x").check_once(), None)
    release["prerelease"] = True
    check("prerelease ignored", await agent.UpdateChecker(current="1.1.0", api=api).check_once(), None)
    release.pop("prerelease")

    # the phone hears about it on connect
    up = agent.UpdateChecker(current="1.1.0", api=api)
    a = web.AppRunner(agent.build_app({"token": "t", "port": 0}, demo=True, updates=up))
    await a.setup()
    await web.TCPSite(a, "127.0.0.1", 18769).start()
    await asyncio.sleep(1)    # first background check
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:18769/ws") as ws:
            await ws.send_json({"type": "auth", "token": "t"})
            m = json.loads((await ws.receive()).data)
    check("auth_ok carries update", (m.get("update") or {}).get("version"), "9.0.0")
    check("auth_ok carries version", m.get("version"), agent.VERSION)
    await a.cleanup()
    await r.cleanup()
    print("ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
