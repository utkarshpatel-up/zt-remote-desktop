#!/usr/bin/env python3
"""Sign-in tests against a fake AV Backup (PocketBase) server; no network needed.

    python test_av_login.py
"""
import asyncio
import json
import sys

import aiohttp
from aiohttp import web

import agent

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


async def start(app, port):
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", port).start()
    return r


async def attempt(s, base, auth):
    async with s.ws_connect(base + "/ws") as ws:
        await ws.send_json(auth)
        m = json.loads((await ws.receive()).data)
        return m["type"] if m["type"] == "auth_ok" else m.get("reason")


async def main():
    pb = await start(fake_pocketbase(), 18091)
    cfg = {"token": "secret-pw", "port": 0, "av_backup_roles": ["admin", "operator"]}
    a1 = await start(agent.build_app(cfg, demo=True, av_server="http://127.0.0.1:18091"), 18766)
    a2 = await start(agent.build_app(cfg, demo=True, av_server="http://127.0.0.1:1"), 18767)
    a3 = await start(agent.build_app(cfg, demo=True, av_server=""), 18768)
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{name}: {got}" + ("" if good else f" (want {want})"))

    av = lambda e, p: {"type": "auth", "method": "av_backup", "email": e, "password": p}
    async with aiohttp.ClientSession() as s:
        b1, b2, b3 = "http://127.0.0.1:18766", "http://127.0.0.1:18767", "http://127.0.0.1:18768"
        check("health shows AV login", (await (await s.get(b1 + "/health")).json())["av_backup"], True)
        check("health without server", (await (await s.get(b3 + "/health")).json())["av_backup"], False)
        check("member signs in", await attempt(s, b1, av("op@team", "pw-op")), "auth_ok")
        check("access password still works", await attempt(s, b1, {"type": "auth", "token": "secret-pw"}), "auth_ok")
        check("wrong AV password", await attempt(s, b1, av("op@team", "nope")), "wrong_credentials")
        check("not a team member", await attempt(s, b1, av("stranger@team", "pw-s")), "not_member")
        check("role not allowed", await attempt(s, b1, av("viewer@team", "pw-v")), "role_not_allowed")
        check("server down", await attempt(s, b2, av("op@team", "pw-op")), "server_unreachable")
        check("password works when server down", await attempt(s, b2, {"type": "auth", "token": "secret-pw"}), "auth_ok")
        check("AV login off", await attempt(s, b3, av("op@team", "pw-op")), "av_backup_not_set_up")
        check("wrong access password", await attempt(s, b3, {"type": "auth", "token": "x"}), "wrong_password")
        for _ in range(4):   # 1 failure above + 4 = 5 -> locked
            await attempt(s, b3, {"type": "auth", "token": "x"})
        check("lockout after 5 failures", await attempt(s, b3, {"type": "auth", "token": "secret-pw"}), "too_many_attempts")

    check("url: bare ip", agent.normalize_server_url("10.7.0.5:8090/"), "http://10.7.0.5:8090")
    check("url: creds refused", agent.normalize_server_url("http://a:b@h"), "")
    check("url: bad scheme", agent.normalize_server_url("ftp://h"), "")
    for r in (pb, a1, a2, a3):
        await r.cleanup()
    print("ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
