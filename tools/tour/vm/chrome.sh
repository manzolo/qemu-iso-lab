#!/bin/sh
# The browser of the recordings: snap Chromium in kiosk mode, zoom 125 %, English UI, DevTools on
# 127.0.0.1:9222 (rec.mjs drives it over an SSH tunnel). Chrome for Testing shows a banner that
# cannot be hidden, hence the distribution's Chromium.
export DISPLAY=:0 LANG=en_US.UTF-8 LANGUAGE=en
exec /snap/bin/chromium --remote-debugging-port=9222 --user-data-dir=$HOME/snap/chromium/common/video-profile \
  --no-first-run --no-default-browser-check --password-store=basic --kiosk --lang=en-US \
  --disable-features=Translate,TranslateUI --force-device-scale-factor=1.25 \
  --window-position=0,0 --window-size=1600,900 about:blank
