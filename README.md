# ZT Remote Desktop

A small remote desktop that runs **only over your ZeroTier network** — the same
network your AV Backup app already uses to sync devices. The computer you want
to control runs the **agent**; you control it from your phone's browser.
No cloud, no account, nothing exposed to the public internet.

- Live screen (JPEG stream), mouse + touch control, keyboard + special keys
- Works on **Windows** and **macOS** computers
- The agent binds **only to the computer's ZeroTier IP address**
- **Two ways to sign in:** your **AV Backup team account** (same e-mail and
  password as in the AV Backup app), or the computer's **access password**
  for people without an account
- Works on computers **without AV Backup or Python** — download the
  standalone program (see 2a)

## 1. Before you start

- ZeroTier installed and **joined to the same network** on the computer and
  your phone (the same network ID you already use for the AV app).
- Find the computer's ZeroTier IP: it's the `10.x.x.x` / `172.x.x.x` address
  shown in the ZeroTier app on that computer (or at my.zerotier.com →
  your network → the computer's node).

## 2a. Easiest: download the standalone program (no Python needed)

Every push to `main` builds ready-to-run programs in GitHub Actions
(**Actions → Test and build → Artifacts**); version tags (`v1.1.0`) also
publish them on the **Releases** page:

- `ZT-Remote-Desktop-Windows.zip` → unzip, double-click `ZT-Remote-Desktop.exe`
  (if SmartScreen warns: *More info → Run anyway*)
- `ZT-Remote-Desktop-macOS-arm64.zip` (M1/M2/M3/M4) or `-intel.zip` → unzip,
  then in Terminal: `xattr -d com.apple.quarantine ZT-Remote-Desktop && ./ZT-Remote-Desktop`
  (the build is not signed by Apple). Grant Screen Recording and
  Accessibility to Terminal as in step 2.

The config file `zt-remote-config.json` is created next to the program.

## 2b. Install from source (Python)

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
3. Sign in, tap **Connect**:
   - **AV Backup account** — your AV Backup e-mail and password. Shown only
     when the computer knows your team server (see below).
   - **Access password** — the password printed by the agent. Use this when
     you have no AV Backup account, or the team server is off.

### Access password

Generated on first run and saved as `"token"` in `zt-remote-config.json`
next to the agent. Keep it private — anyone on your ZeroTier network with it
can control the computer. To change it (e.g. to something easier to type),
edit `"token"` in that file and restart.

### AV Backup account sign-in

The agent checks your e-mail and password live with the team's PocketBase
server (the same one AV Backup syncs with) and lets you in only if your
account is an **active team member** with an allowed role. Deactivate
someone in the PocketBase dashboard and they can no longer connect. Your
password goes to the team server only; it is never saved on the computer or
in the phone browser.

- **AV Backup installed on this computer and team sync set up:** nothing to
  do — the agent reads the server address from the app.
- **AV Backup not on this computer:** give the address once, it is saved:
  `ZT-Remote-Desktop --av-server http://10.7.0.5:8090` (or
  `python agent.py --av-server ...`), or set `"av_backup_server"` in
  `zt-remote-config.json`. `--av-server ""` turns it off again.
- **Who may connect:** `"av_backup_roles"` in the config, default
  `["admin", "operator"]`. Set `["admin"]` to allow admins only.

After 5 wrong tries from one device, sign-in is blocked for a minute.

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
- **Wrong access password:** the one on the agent's console / `"token"` in
  `zt-remote-config.json` is the source of truth.
- **No "AV Backup account" option on the phone:** the agent didn't find the
  team server — its console says `AV Backup login: OFF`. Use `--av-server`.
- **"team server could not be reached":** the PocketBase computer is off or
  not on ZeroTier. The access password still works.

## 7. Test mode

`python agent.py --demo` streams an animated test pattern and logs input
instead of controlling anything — handy for checking the phone connection
before granting permissions.

`python test_av_login.py` checks both sign-in methods against a fake team
server (no network needed).

`python test_client.py http://<ip>:8765 <token>` checks the protocol against a
running agent. With an agent started as `--demo --demo-still`, add `--still`
to also check that an unchanged screen is not sent again.

## How it works

`agent.py` (aiohttp) serves `client.html`, streams screen captures from
`mss` as JPEG frames over an authenticated WebSocket (access password, or
an AV Backup account verified with the team's PocketBase server), and injects input
with `pynput`. Interface detection uses `psutil`. That's the whole design —
one file each side, no database, no server in the middle.
