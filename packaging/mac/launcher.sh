#!/bin/bash
# Main executable of "ZT Remote Desktop.app". The app carries one program for
# Apple Silicon and one for Intel; this picks the right one and runs it in
# Terminal, where the address and access password are shown.
APP="$(cd "$(dirname "$0")/../.." && pwd)"
# downloaded apps are quarantined; the user already chose to open this one
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
ARCH=x86_64
# sysctl, not uname: uname says x86_64 when launched under Rosetta
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then ARCH=arm64; fi
exec open -a Terminal "$APP/Contents/Resources/$ARCH/ZT-Remote-Desktop"
