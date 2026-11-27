#!/usr/bin/env python3
"""Sign-in tests against a fake AV Backup (PocketBase) server; no network needed.

    python tests/test_av_login.py
"""
import asyncio
import json
import sys

import aiohttp
from aiohttp import web

from harness import Agent

USERS = {  # email: (password, avb_me result or None = not a member)
    "op@team": ("pw-op", {"role": "operator", "display_name": "Op One"}),
    "viewer@team": ("pw-v", {"role": "viewer", "display_name": "V"}),
    "stranger@team": ("pw-s", None),
}


def fake_pocketbase():
    app = web.Application()

    async def login(req):
        b = await req.json()
        u = USERS.get(b.get("identity"))
        if not u or u[0] != b.get("password"):
            return web.json_response({"message": "Failed to authenticate."}, status=400)
        return web.json_response({"token": "tok:" + b["identity"], "record": {"id": "x"}})

    async def me(req):
        email = req.headers.get("Authorization", "").removeprefix("tok:")
        if email not in USERS:
            return web.json_response({}, status=401)
        m = USERS[email][1]
        if m is None:
            return web.json_response({"code": "42501", "message": "avb:not_member"}, status=403)
        return web.json_response(dict(m, protocol_version=1, user_id="x"))

    app.router.add_post("/api/collections/users/auth-with-password", login)
    app.router.add_post("/api/avb/rpc/avb_me", me)
    return app


async def attempt(s, base, auth):
    async with s.ws_connect(base + "/ws") as ws:
        await ws.send_json(auth)
        m = json.loads((await ws.receive()).data)
        return m["type"] if m["type"] == "auth_ok" else m.get("reason")


async def main():
    runner = web.AppRunner(fake_pocketbase())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18091).start()
    a1 = Agent(18766, av_server="http://127.0.0.1:18091", token="secret-pw")
    a2 = Agent(18767, av_server="http://127.0.0.1:1", token="secret-pw")
    a3 = Agent(18768, av_server="", token="secret-pw")
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{name}: {got}" + ("" if good else f" (want {want})"))

    av = lambda e, p: {"type": "auth", "method": "av_backup", "email": e, "password": p}
    try:
        h1, _, h3 = await a1.ready(), await a2.ready(), await a3.ready()
        check("health shows AV login", h1["av_backup"], True)
        check("health without server", h3["av_backup"], False)
        async with aiohttp.ClientSession() as s:
            b1, b2, b3 = a1.base, a2.base, a3.base
            check("member signs in", await attempt(s, b1, av("op@team", "pw-op")), "auth_ok")
            check("access password still works", await attempt(s, b1, {"type": "auth", "token": "secret-pw"}), "auth_ok")
            check("wrong AV password", await attempt(s, b1, av("op@team", "nope")), "wrong_credentials")
            check("not a team member", await attempt(s, b1, av("stranger@team", "pw-s")), "not_member")
            check("role not allowed", await attempt(s, b1, av("viewer@team", "pw-v")), "role_not_allowed")
            check("server down", await attempt(s, b2, av("op@team", "pw-op")), "server_unreachable")
            check("password works when server down", await attempt(s, b2, {"type": "auth", "token": "secret-pw"}), "auth_ok")
            check("AV login off", await attempt(s, b3, av("op@team", "pw-op")), "av_backup_not_set_up")
            check("wrong access password", await attempt(s, b3, {"type": "auth", "token": "x"}), "wrong_password")
            check("not JSON", await attempt(s, b3, "hello"), "wrong_password")
            for _ in range(3):   # 2 failures above + 3 = 5 -> locked
                await attempt(s, b3, {"type": "auth", "token": "x"})
            check("lockout after 5 failures", await attempt(s, b3, {"type": "auth", "token": "secret-pw"}), "too_many_attempts")
    finally:
        for a in (a1, a2, a3):
            a.stop()
        await runner.cleanup()
    print("ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
