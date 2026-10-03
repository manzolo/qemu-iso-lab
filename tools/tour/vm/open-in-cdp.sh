#!/bin/sh
# BROWSER for the recordings: the URL becomes a tab of the recorded Chromium, which comes to the front.
curl -s -X PUT "http://127.0.0.1:9222/json/new?$1" >/dev/null
DISPLAY=:0 xdotool search --class chromium windowactivate 2>/dev/null
