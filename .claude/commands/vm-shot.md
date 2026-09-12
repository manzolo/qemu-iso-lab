---
description: Screenshot a running lab VM (also during a bootstrap) through its QMP socket and look at it
category: vm
argument-hint: <vm> [--no-wake]
allowed-tools:
  - Bash
  - Read
---

Take a screenshot of the VM `$ARGUMENTS` and look at it. The socket is
`artifacts/<vm>/runtime/qmp.sock`, present for background VMs and during bootstraps; if it
is missing, `./bin/vmctl status <vm>` and say the VM is not running.

Run this from the repository root (replace `<vm>`, `<out>` is a PNG in the scratchpad,
`<wake>` is `1` unless `--no-wake` was given):

```bash
python3 - artifacts/<vm>/runtime/qmp.sock <out>.png <wake> <<'PYEOF'
import json, socket, struct, sys, time, zlib, os
sock, out, wake = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
s = socket.socket(socket.AF_UNIX); s.settimeout(15); s.connect(sock); f = s.makefile("rw")
f.readline(); f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n"); f.flush(); f.readline()
if wake:  # a blanked console is captured black otherwise; Shift changes nothing in the guest
    f.write(json.dumps({"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "shift"}]}}) + "\n"); f.flush(); f.readline(); time.sleep(3)
ppm = "/tmp/claude-1000/vmshot-%d.ppm" % os.getpid()
f.write(json.dumps({"execute": "screendump", "arguments": {"filename": ppm}}) + "\n"); f.flush(); f.readline(); time.sleep(1)
data = open(ppm, "rb").read(); parts = data.split(maxsplit=4); w, h = int(parts[1]), int(parts[2]); raw = parts[4][:w * h * 3]
rows = b"".join(b"\x00" + raw[y * w * 3:(y + 1) * w * 3] for y in range(h))
chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(out, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
os.unlink(ppm); print(out, w, h)
PYEOF
```

Never wake during an installer that could take the keystroke as an answer (a d-i prompt,
Windows Setup): pass `--no-wake` there. Then Read the PNG and describe what the guest shows
in one or two sentences: which screen, whether it waits for input.
