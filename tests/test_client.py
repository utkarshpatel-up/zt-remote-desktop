#!/usr/bin/env python3
"""Tiny protocol test: auth (good + bad token), receive frames, send input.

    python test_client.py http://<ip>:8765 <token>            # any agent
    python test_client.py http://<ip>:8765 <token> --still    # agent run with --demo --demo-still
"""
import asyncio
import json
import sys

import aiohttp


async def main(base, token, still):
    async with aiohttp.ClientSession() as s:
        # bad token must be rejected
        async with s.ws_connect(base + "/ws") as ws:
            await ws.send_json({"type": "auth", "token": "wrong"})
            msg = await ws.receive()
            assert msg.data and json.loads(msg.data)["type"] == "auth_failed", msg
        print("PASS bad token rejected")

        frames = 0
        async with s.ws_connect(base + "/ws") as ws:
            await ws.send_json({"type": "auth", "token": token})
            # nonsense settings must not stop the stream
            await ws.send_json({"type": "config", "fps": "fast", "quality": None,
                                "max_width": -5})
            await ws.send_json({"type": "config", "fps": 20, "quality": 60,
                                "max_width": 1280})
            await ws.send_json({"type": "mouse_move", "x": 0.25, "y": 0.5})
            await ws.send_json({"type": "mouse_move_rel", "dx": 0.25, "dy": -0.25})
            await ws.send_json({"type": "mouse_down", "button": "left"})
            await ws.send_json({"type": "mouse_up", "button": "left"})
            await ws.send_json({"type": "key", "key": "ArrowUp"})
            await ws.send_json({"type": "key", "key": "c", "modifiers": ["ctrl"]})
            await ws.send_json({"type": "text", "text": "hello"})
            await ws.send_json(["not", "an", "object"])
            platform = None
            cursor = None
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    assert msg.data[:2] == b"\xff\xd8", "not a JPEG frame"
                    frames += 1
                    if frames >= (1 if still else 3):
                        break
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    assert m["type"] in ("auth_ok", "meta"), m
                    if m["type"] == "auth_ok":
                        platform = m.get("platform")
                    if m["type"] == "meta" and "cx" in m:
                        cursor = (m["cx"], m["cy"])
            assert platform in ("mac", "windows", "linux"), platform
            print(f"PASS auth ok (platform {platform}), received {frames} JPEG "
                  f"frame(s), input sent")

            # the cursor ends where the moves put it: 0.25 + 0.25, 0.5 - 0.25
            for _ in range(20):
                if cursor and abs(cursor[0] - 0.5) < 0.01 and abs(cursor[1] - 0.25) < 0.01:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), 0.5)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    if m.get("type") == "meta" and "cx" in m:
                        cursor = (m["cx"], m["cy"])
            assert cursor and abs(cursor[0] - 0.5) < 0.01 and abs(cursor[1] - 0.25) < 0.01, cursor
            print(f"PASS cursor reported at {cursor} after absolute + relative moves")

            if still:
                extra = 0
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), 1.5)
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            extra += 1
                except asyncio.TimeoutError:
                    pass
                assert extra == 0, f"{extra} unchanged frames were resent"
                print("PASS still screen: no frames resent")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = args[0] if args else "http://127.0.0.1:8765"
    asyncio.run(main(base, args[1], "--still" in sys.argv))
