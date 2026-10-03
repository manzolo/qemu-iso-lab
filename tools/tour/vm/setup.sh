#!/bin/sh
# Inside the recording VM (an Ubuntu/Lubuntu desktop with X11, autologin on :0), once:
#   sh ~/lab/video/setup.sh
# Packages (asks for sudo), the Chromium snap, the files of ~/lab/video, qterminal without menu
# and tab bar, Chromium without password prompts. Idempotent.
set -eu
cd "$(dirname "$0")"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg xdotool qterminal curl git python3 >/dev/null
snap list chromium >/dev/null 2>&1 || sudo snap install chromium
chmod +x mv.py chrome.sh open-in-cdp.sh
ini="$HOME/.config/qterminal.org/qterminal.ini"
if [ -f "$ini" ]; then
    sed -i 's/^HideTabBarWithOneTab=.*/HideTabBarWithOneTab=true/; s/^MenuVisible=.*/MenuVisible=false/; s/^fontSize=.*/fontSize=17/' "$ini"
fi
# A first start creates the profile; then the password manager and autofill go off.
prefs="$HOME/snap/chromium/common/video-profile/Default/Preferences"
if [ ! -f "$prefs" ]; then
    timeout 15 ./chrome.sh >/dev/null 2>&1 || true
fi
python3 - "$prefs" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
if p.is_file():
    d = json.loads(p.read_text())
    d["credentials_enable_service"] = False
    d.setdefault("profile", {})["password_manager_enabled"] = False
    d.setdefault("autofill", {})["profile_enabled"] = False
    p.write_text(json.dumps(d))
PY
echo "recording VM ready: start a session from the host with tools/tour/session.sh"
