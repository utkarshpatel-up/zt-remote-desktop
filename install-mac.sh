#!/bin/bash
cd "$(dirname "$0")"
echo "Installing ZT Remote Desktop (macOS)..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/ or run: brew install python"
  exit 1
fi
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo
echo "Done. Run ./start-mac.sh to launch the agent."
echo "macOS will ask for two permissions the first time (see README):"
echo "  1. Screen Recording   - so the agent can see the screen"
echo "  2. Accessibility      - so it can move the mouse / type"
