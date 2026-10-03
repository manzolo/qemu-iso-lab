#!/usr/bin/env python3
"""Glide the X pointer to x,y with an ease-in-out curve; optional click: mv.py X Y [SECONDS] [click]."""
import os
import subprocess
import sys
import time

os.environ.setdefault("DISPLAY", ":0")
x, y = int(float(sys.argv[1])), int(float(sys.argv[2]))
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
click = len(sys.argv) > 4 and sys.argv[4] == "click"
out = subprocess.run(["xdotool", "getmouselocation", "--shell"], capture_output=True, text=True).stdout
pos = dict(line.split("=") for line in out.split() if "=" in line)
x0, y0 = int(pos["X"]), int(pos["Y"])
steps = max(8, int(dur * 60))
for i in range(1, steps + 1):
    t = i / steps
    e = 3 * t * t - 2 * t * t * t
    subprocess.run(["xdotool", "mousemove", str(round(x0 + (x - x0) * e)), str(round(y0 + (y - y0) * e))])
    time.sleep(dur / steps)
if click:
    time.sleep(0.15)
    subprocess.run(["xdotool", "click", "1"])
