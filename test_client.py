#!/usr/bin/env python3
"""Tiny protocol test: auth (good + bad token), receive frames, send input."""
import asyncio
import json
import sys

import aiohttp


async def main(base, token):
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
            await ws.send_json({"type": "config", "fps": 20, "quality": 60,
                                "max_width": 1280})
            await ws.send_json({"type": "mouse_move", "x": 0.25, "y": 0.5})
            await ws.send_json({"type": "mouse_down", "x": 0.25, "y": 0.5,
                                "button": "left"})
            await ws.send_json({"type": "mouse_up", "button": "left"})
            await ws.send_json({"type": "key", "key": "c", "modifiers": ["ctrl"]})
            await ws.send_json({"type": "text", "text": "hello"})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    assert msg.data[:2] == b"\xff\xd8", "not a JPEG frame"
                    frames += 1
                    if frames >= 3:
                        break
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    t = json.loads(msg.data)["type"]
                    assert t in ("auth_ok", "meta", "error"), t
        print(f"PASS auth ok, received {frames} JPEG frames, input sent")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
    token = sys.argv[2]
    asyncio.run(main(base, token))
