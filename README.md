# ZT Remote Desktop

A small remote desktop that runs **only over your ZeroTier network** — the same
network your AV Backup app already uses to sync devices. The computer you want
to control runs the **agent**; you control it from your phone's browser.
No cloud, no account, nothing exposed to the public internet.

- Live screen (JPEG stream), mouse + touch control, keyboard + special keys
- Works on **Windows** and **macOS** computers
- The agent binds **only to the computer's ZeroTier IP address** and every
  connection needs the **access token** printed at startup

## 1. Before you start

- ZeroTier installed and **joined to the same network** on the computer and
  your phone (the same network ID you already use for the AV app).
- Find the computer's ZeroTier IP: it's the `10.x.x.x` / `172.x.x.x` address
  shown in the ZeroTier app on that computer (or at my.zerotier.com →
  your network → the computer's node).

## 2. Install on the computer you want to control

### Windows
1. Install Python 3.10+ from python.org — tick **"Add python.exe to PATH"**.
2. Double-click `install.bat` (one time only).
3. Double-click `start.bat` every time you want the agent running.

### macOS
1. Install Python 3.10+ (python.org or `brew install python`).
2. In Terminal, in this folder: `./install-mac.sh` (one time only).
3. To run: `./start-mac.sh`
4. **Two permissions macOS will ask for** (System Settings → Privacy & Security):
   - **Screen Recording** → enable for Terminal (or the app you run it from)
   - **Accessibility** → enable for Terminal as well
   Restart the agent after granting them.

## 3. Connect from your phone

1. Start the agent on the computer. It prints something like:

   ```
   Listening on: 10.147.20.5:8765
   Open on your phone: http://10.147.20.5:8765
   Access token: AbCdEf...
   ```

2. On your phone (ZeroTier connected), open that address in the browser.
3. Paste the **access token**, tap **Connect**.

On first run the token is generated and saved in `zt-remote-config.json`
next to `agent.py`. Keep it private — anyone on your ZeroTier network with
the token can control the computer. To change it, edit that file and restart.

## 4. Using it

- **Mouse (desktop browser):** move / click / right-click / scroll as normal.
  Click the screen once and your physical keyboard types on the computer
  (arrows, Esc, shortcuts included).
- **Touch (phone)**, two modes (the **Touch** menu; your choice is remembered):
  - **Trackpad** (default): drag anywhere to move the pointer, like a laptop
    trackpad · tap = click where the pointer is · two-finger tap = right-click.
  - **Direct**: the pointer jumps to your finger · tap = click there.
  - In both: press & hold = right-click · press & hold, then drag = drag
    (move windows, select text) · two-finger drag = scroll (up/down and
    sideways) · pinch = zoom in, **Fit screen** zooms back out. While zoomed
    in trackpad mode the view follows the pointer.
- The pointer is drawn on the picture, so you can see where it is.
- **Keys bar:** Esc, Tab, Enter, Win/⌘, Ctrl+C / Ctrl+V / Ctrl+Z, Alt+Tab, arrows.
  When the computer is a Mac they become ⌘C / ⌘V / ⌘Z / ⌘Tab.
- **Type box:** write text and Send to type it on the computer. The
  "Phone keyboard" button summons your phone keyboard for live typing
  (backspace and Enter included).
- **Quality / Width / FPS:** lower them if the picture lags (ZeroTier direct
  connections handle 1280px @ 12 FPS / quality 60 comfortably). A still
  screen sends nothing, so an idle computer uses almost no bandwidth.

## 5. Run at startup (optional)

- **Windows:** press `Win+R`, type `shell:startup`, and drop a shortcut to
  `start.bat` in that folder.
- **macOS:** System Settings → General → Login Items → add `start-mac.sh`
  (or run it from a terminal when needed).

## 6. Troubleshooting

- **Page doesn't open on the phone:** check ZeroTier shows *Connected* on both
  devices and that you're using the computer's ZeroTier IP, not its Wi-Fi IP.
  Windows may ask to allow Python through the firewall the first time — allow
  it for private networks.
- **"Waiting for ZeroTier to connect...":** the agent listens only on
  ZeroTier, so it waits until ZeroTier is up (normal right after login) and
  then starts by itself. If it keeps waiting, check the ZeroTier app shows
  the computer as Connected. You can force an address with
  `python agent.py --bind 10.147.20.5`, or listen on every network
  (token still required) with `--bind all`.
- **Black screen on Mac:** Screen Recording permission is missing (step 2).
- **Screen visible but clicks do nothing on Mac:** Accessibility permission
  is missing (step 2).
- **Wrong token:** the token on the agent's console / in
  `zt-remote-config.json` is the source of truth.

## 7. Test mode

`python agent.py --demo` streams an animated test pattern and logs input
instead of controlling anything — handy for checking the phone connection
before granting permissions.

`python test_client.py http://<ip>:8765 <token>` checks the protocol against a
running agent. With an agent started as `--demo --demo-still`, add `--still`
to also check that an unchanged screen is not sent again.

## How it works

`agent.py` (aiohttp) serves `client.html`, streams screen captures from
`mss` as JPEG frames over a token-authenticated WebSocket, and injects input
with `pynput`. Interface detection uses `psutil`. That's the whole design —
one file each side, no database, no server in the middle.
