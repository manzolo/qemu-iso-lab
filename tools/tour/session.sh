#!/bin/sh
# Start a recording session on the recording VM (docs/TOUR.md):
#   TOUR_VM=user@host tools/tour/session.sh [--setup]
# --setup copies tools/tour/vm/ to ~/lab/video/ and runs its setup.sh (packages, Chromium...; asks
# for sudo in the VM). Every session: the screen at 1600x900 and kept there (spice-vdagent stopped:
# an open viewer would resize the guest to its window), no blanking, the recorded Chromium started,
# DevTools tunnelled to 127.0.0.1:19222 on the host, where rec.mjs connects.
set -eu
: "${TOUR_VM:?TOUR_VM=user@host of the recording VM}"
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "${1:-}" = "--setup" ]; then
    ssh "$TOUR_VM" 'mkdir -p ~/lab/video ~/.cache/ms-playwright'
    scp -q "$HERE"/vm/* "$TOUR_VM":lab/video/
    ssh -t "$TOUR_VM" 'sh ~/lab/video/setup.sh'
fi
ssh -o BatchMode=yes "$TOUR_VM" 'pkill -x spice-vdagent; export DISPLAY=:0; xrandr --output Virtual-1 --mode 1600x900; xset s off; xset s noblank; pkill -f update-notifie[r]; true'
ssh -o BatchMode=yes "$TOUR_VM" 'pkill -f "chromium-browse[r]/chrome --pass"; true'
ssh -o BatchMode=yes "$TOUR_VM" 'sleep 2; setsid -f ~/lab/video/chrome.sh >/tmp/chrome.log 2>&1 </dev/null; curl -s --retry 20 --retry-delay 1 --retry-connrefused 127.0.0.1:9222/json/version >/dev/null && echo "chromium up"'
pkill -f "127.0.0.1:19222:127.0.0.1:922[2]" || true
setsid -f ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L 127.0.0.1:19222:127.0.0.1:9222 "$TOUR_VM" </dev/null >/dev/null 2>&1
echo "session ready: TOUR_VM=$TOUR_VM PLAYWRIGHT_MODULE=... node tools/tour/rec.mjs tools/tour/clips/<clip>.mjs"
