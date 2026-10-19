#!/usr/bin/env python3
"""Glide the X pointer to x,y with an ease-in-out curve; optional click: mv.py X Y [SECONDS] [click].

One process moves the pointer with python-Xlib (warp_pointer, a click through XTEST), paced by the
clock: one xdotool process per step, starved by ffmpeg's x11grab, stretched a 0.8 s glide to 14 s
in the video (office studio, 2026-10-19). Without python3-xlib it falls back to xdotool."""
import os
import subprocess
import sys
import time

os.environ.setdefault("DISPLAY", ":0")
x, y = int(float(sys.argv[1])), int(float(sys.argv[2]))
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
click = len(sys.argv) > 4 and sys.argv[4] == "click"


def ease(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


try:
    from Xlib import X, display
    from Xlib.ext import xtest
except ImportError:
    out = subprocess.run(["xdotool", "getmouselocation", "--shell"], capture_output=True, text=True).stdout
    pos = dict(line.split("=") for line in out.split() if "=" in line)
    x0, y0 = int(pos["X"]), int(pos["Y"])
    steps = max(8, int(dur * 60))
    for i in range(1, steps + 1):
        e = ease(i / steps)
        subprocess.run(["xdotool", "mousemove", str(round(x0 + (x - x0) * e)), str(round(y0 + (y - y0) * e))])
        time.sleep(dur / steps)
    if click:
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"])
    sys.exit(0)

d = display.Display()
root = d.screen().root
pointer = root.query_pointer()
x0, y0 = pointer.root_x, pointer.root_y
start = time.monotonic()
while True:
    # The position follows the clock, not a step count: a late frame skips ahead instead of
    # stretching the glide.
    t = min(1.0, (time.monotonic() - start) / dur) if dur > 0 else 1.0
    e = ease(t)
    root.warp_pointer(round(x0 + (x - x0) * e), round(y0 + (y - y0) * e))
    d.sync()
    if t >= 1.0:
        break
    time.sleep(1 / 120)
if click:
    time.sleep(0.15)
    xtest.fake_input(d, X.ButtonPress, 1)
    d.sync()
    xtest.fake_input(d, X.ButtonRelease, 1)
    d.sync()
