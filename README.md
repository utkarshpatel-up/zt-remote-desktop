# ZT Remote Desktop

Control a Windows PC or a Mac from your phone, **only over your ZeroTier
network** (the same network your AV Backup app uses). No cloud and no account
service. Nothing is exposed to the public internet.

- Live screen, mouse/touch control, keyboard and special keys, all in the phone's browser
- **Two ways to sign in:** your **AV Backup team account** (same e-mail and
  password as in the AV Backup app), or the computer's **access password** for
  people without an account
- Works on computers **without AV Backup**: it's a normal app, nothing else to install
- **Updates itself:** the app tells you when a new version is out, and
  **Update now** installs it
- Built with **Tauri 2 + Rust**, so Android and iOS apps can come later from the same code

## 1. Download

From the [latest release](https://github.com/utkarshpatel-up/zt-remote-desktop/releases/latest):

| Computer | File | Notes |
|---|---|---|
| Windows (install) | `ZT-Remote-Desktop_<v>_x64-setup.exe` | Installs for your user, no admin rights. Updates from inside the app. |
| Windows (portable) | `ZT-Remote-Desktop_<v>_Windows-Portable.zip` | Unzip anywhere and run `ZT Remote Desktop.exe`. Settings are kept next to the .exe. |
| Mac (Apple Silicon and Intel) | `ZT-Remote-Desktop_<v>_universal.dmg` | Drag to Applications. First time: right-click → **Open**. |

The builds aren't code-signed by Microsoft or Apple:
- **Windows:** SmartScreen may warn once: **More info → Run anyway**. Allow
  it through the firewall (private networks) when Windows asks.
- **Mac:** the app asks for two permissions in System Settings → Privacy &
  Security: **Screen Recording** (to see the screen) and **Accessibility** (to
  move the mouse and type). The app window has buttons that open both. Quit
  and reopen the app after switching them on.

## 2. Before you start

ZeroTier must be installed and **joined to the same network** on the computer
and on your phone. The app listens only on the computer's ZeroTier address, so
it waits ("Waiting for ZeroTier…") until ZeroTier is connected.

## 3. Connect from your phone

1. Start the app on the computer. Its window shows the address, e.g.
   `http://10.147.20.5:8765`.
2. On your phone (with ZeroTier on), open that address in the browser.
3. Sign in and tap **Connect**:
   - **AV Backup account:** your AV Backup e-mail and password.
   - **Access password:** the password shown in the app window. Use this if
     you have no AV Backup account, or if the team server is off.

Closing the window keeps remote access running in the tray / menu bar. To stop
it, use the tray icon → **Quit**. Tick **Start when I sign in** to have it
always available.

### Access password

Created on first run. You can show, copy, change or randomize it in the app.
Anyone on your ZeroTier network with it can control the computer, so keep it
private. After 5 wrong tries from one device, sign-in is blocked for a minute.

### AV Backup account sign-in

The app checks the e-mail and password live with the team's PocketBase server
(the one AV Backup syncs with). It lets someone in only if their account is
an **active team member** with an allowed role. Deactivate someone in the
PocketBase dashboard and they can't connect any more. The password goes to the
team server only and is never stored.

**AV Backup itself needs no changes:** this uses the sign-in and membership
check that the team server already has.

- **If AV Backup is installed on this computer** with team sync set up,
  nothing to do: the server address is read from it.
- **If not,** type the team server address (e.g. `http://10.7.0.5:8090`) under
  **AV Backup sign-in** and press Save.
- **Who may connect:** admins and/or operators (both by default).

**"Connected now"** lists who is connected, and **Disconnect all** drops them.

## 4. Using it on the phone

- **Touch:** two modes, chosen in the **Touch** menu:
  - **Trackpad** (default): drag to move the pointer, tap to click.
  - **Direct:** the pointer jumps to your finger.
  - In both modes: press and hold = right-click · hold then drag = drag ·
    two-finger drag = scroll · pinch = zoom (**Fit screen** zooms back out).
- **Keys bar:** Esc, Tab, Enter, Win/⌘, copy, paste, undo, Alt+Tab and the
  arrow keys. On a Mac these become ⌘C / ⌘V / ⌘Z / ⌘Tab.
- **Type box** and the **Phone keyboard** button for typing text.
- **Quality / Width / FPS:** lower them if the picture lags. A still screen
  sends nothing.

## 5. Updates

- **Installed (Windows Setup or Mac app):** when a new version is out, the app
  window shows **Update now**. It downloads the update, checks its signature,
  installs it and restarts. Your password and settings are kept.
- **Portable Windows copy:** the app shows that an update exists. Download
  the new zip and replace the .exe.
- The phone page also shows "Update available for this PC" when you connect.

## 6. Troubleshooting

- **Page doesn't open on the phone:** check that ZeroTier shows *Connected* on
  both devices, and that you're using the ZeroTier address shown in the app,
  not the Wi-Fi address.
- **No "AV Backup account" choice on the phone:** AV Backup sign-in is off on
  that computer. Enter the team server address in the app.
- **"team server could not be reached":** the PocketBase computer is off or
  not on ZeroTier. The access password still works.
- **Black screen, or clicks do nothing, on a Mac:** a permission is missing
  (see section 1). After an update, macOS may ask for the permissions again.

## For developers

```
npm install                     # Tauri CLI
npx tauri dev                   # run the app
npx tauri build                 # installers for this OS
cargo test --manifest-path src-tauri/Cargo.toml
```

On Linux, install the libraries listed in `.github/workflows/build.yml` first.

- `src-tauri/src/host/`: the host, in Rust. The server (axum, HTTP and
  WebSocket) is in `server.rs`; screen capture (`xcap` → JPEG) in
  `capture.rs`; mouse and keyboard (`enigo`) in `input.rs`; sign-in in
  `auth.rs`; the update notice in `update.rs`.
- `src-tauri/src/app.rs`: the command line, the window and tray, and the in-app updater.
- `ui/index.html`: the app window. `ui/viewer.html`: the phone page, served by
  the host (and the starting point for a future Android/iOS viewer).
- `--headless --demo` runs the server alone with a test pattern. The black-box
  tests in `tests/` use it (`pip install aiohttp`, then
  `python tests/test_av_login.py`, `tests/test_update.py`,
  `tests/test_client.py <url> <password>`).

**Making a release:** bump `version` in `src-tauri/tauri.conf.json`,
`src-tauri/Cargo.toml` and `package.json`, merge to `main`, then push a tag
`v<version>`. CI builds all three packages, signs the update bundles with the
`TAURI_SIGNING_PRIVATE_KEY` secret, publishes the release and `latest.json`,
and installed apps then offer the update.
