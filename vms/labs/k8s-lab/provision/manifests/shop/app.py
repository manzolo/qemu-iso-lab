"""shop: the Kubernetes course's mini app (vms/labs/k8s-lab, course-k8s-*).

A shopping list kept in Redis and notes kept as objects in RustFS (an S3 server like MinIO,
whose public images MinIO stopped publishing in 2025), served on port 8080. Standard
library only, so it runs from a ConfigMap on the stock python image with nothing to build: Redis
is spoken in RESP over a socket, RustFS through S3 requests signed with AWS Signature V4.

  GET  /         the page: the list, the notes, which pod answered, a visit counter
  POST /add      item=...     RPUSH onto the list
  POST /note     text=...     PUT an object into the bucket
  GET  /whoami   {"pod", "ip", "node"} of the pod that answered: the page polls it, and since every
                 answer closes its connection (HTTP/1.0) the Service picks a pod for each one, so the
                 panel shows the replicas taking turns, a scale, a node drained
  GET  /work     a few hundred milliseconds of CPU (what the autoscaler sees under load)
  GET  /healthz  liveness: the process answers
  GET  /ready    readiness: Redis answers PING (503 otherwise: the pod leaves the Service)
"""
import datetime
import hashlib
import hmac
import html
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
S3 = os.environ.get("S3_ENDPOINT", "rustfs:9000")
BUCKET = os.environ.get("BUCKET", "notes")
ACCESS = os.environ.get("S3_ACCESS_KEY", "")
SECRET = os.environ.get("S3_SECRET_KEY", "")
TITLE = os.environ.get("TITLE", "Shopping list")
COLOR = os.environ.get("COLOR", "#2f6f4f")
POD = socket.gethostname()
POD_IP = os.environ.get("POD_IP", "?")        # the downward API fills these two
NODE = os.environ.get("NODE_NAME", "?")


def redis(*args: str) -> object:
    """One command, one reply (RESP2): enough for PING, INCR, RPUSH and LRANGE."""
    with socket.create_connection((REDIS_HOST, 6379), timeout=2) as s:
        s.sendall(b"*%d\r\n" % len(args) + b"".join(b"$%d\r\n%s\r\n" % (len(a.encode()), a.encode()) for a in args))
        f = s.makefile("rb")

        def read() -> object:
            line = f.readline().rstrip(b"\r\n")
            kind, rest = line[:1], line[1:]
            if kind in (b"+", b":"):
                return rest.decode()
            if kind == b"-":
                raise RuntimeError(rest.decode())
            if kind == b"$":
                n = int(rest)
                return None if n < 0 else f.read(n + 2)[:-2].decode()
            if kind == b"*":
                return [read() for _ in range(int(rest))]
            raise RuntimeError(f"unexpected reply {line!r}")
        return read()


def s3(method: str, path: str, query: str = "", body: bytes = b"") -> bytes:
    """One S3 request, signed with Signature V4 (region us-east-1, the default of S3 servers)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = hashlib.sha256(body).hexdigest()
    headers = {"host": S3, "x-amz-content-sha256": payload, "x-amz-date": amz}
    signed = ";".join(sorted(headers))
    canonical = "\n".join([method, urllib.parse.quote(path), query,
                           "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload])
    scope = f"{day}/us-east-1/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + SECRET).encode()
    for part in (day, "us-east-1", "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={ACCESS}/{scope}, SignedHeaders={signed}, Signature={signature}"
    url = f"http://{S3}{urllib.parse.quote(path)}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, data=body if method == "PUT" else None, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read()


def bucket(name: str) -> None:
    """Create the bucket unless it exists (409)."""
    try:
        s3("PUT", f"/{name}")
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise


def notes() -> list:
    bucket(BUCKET)
    listing = ElementTree.fromstring(s3("GET", f"/{BUCKET}", "list-type=2"))
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    keys = sorted((k.text or "" for k in listing.iterfind("s3:Contents/s3:Key", ns)), reverse=True)
    return [(k, s3("GET", f"/{BUCKET}/{k}").decode(errors="replace")) for k in keys[:10]]


def page() -> str:
    try:
        visits = redis("INCR", "visits")
        items = redis("LRANGE", "list", "0", "-1") or []
        list_html = "".join(f"<li>{html.escape(i)}</li>" for i in items) or "<li><em>empty</em></li>"
    except OSError as e:
        visits, list_html = "?", f"<li class=err>Redis unreachable: {html.escape(str(e))}</li>"
    try:
        notes_html = "".join(f"<li><code>{html.escape(k)}</code> {html.escape(t)}</li>" for k, t in notes()) or "<li><em>none</em></li>"
    except (OSError, urllib.error.URLError) as e:
        notes_html = f"<li class=err>Object storage unreachable: {html.escape(str(e))}</li>"
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>{html.escape(TITLE)}</title>
<style>body{{font:18px system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em}}
h1{{color:{COLOR}}}.pod{{background:{COLOR};color:#fff;padding:.3em .7em;border-radius:.4em}}
.err{{color:#b00}}.row{{font:15px ui-monospace,monospace;margin:.15em 0;height:20px}}
#live{{height:180px;overflow:hidden}}
.dot{{display:inline-block;width:.8em;height:.8em;border-radius:50%;margin-right:.5em}}form{{margin:.6em 0}}input{{font:inherit}}</style>
<h1>{html.escape(TITLE)}</h1>
<p>This page: pod <span class=pod>{html.escape(POD)}</span> {html.escape(POD_IP)} on <b>{html.escape(NODE)}</b> &middot; visit {visits}</p>
<h2>Who answers <small>(a request every second)</small></h2><div id=live></div>
<h2>List <small>(Redis)</small></h2><ul>{list_html}</ul>
<form method=post action=add><input name=item placeholder="Something to buy" required> <button>Add</button></form>
<h2>Notes <small>(RustFS, bucket {html.escape(BUCKET)})</small></h2><ul>{notes_html}</ul>
<form method=post action=note><input name=text placeholder="A note" required> <button>Save</button></form>
<script>
const colors = {{}}, palette = ["#2f6f4f", "#8a3ffc", "#d1542b", "#1f6fb2", "#a07800"], seen = [];
async function tick() {{
  let row;
  // A Service with no ready pod does not refuse, it lets the connection hang: give up after
  // 900 ms, or the panel would freeze on its last answers instead of saying "no answer".
  try {{ const r = await fetch("whoami", {{cache: "no-store", signal: AbortSignal.timeout(900)}}); row = await r.json(); }}
  catch (e) {{ row = {{pod: "no answer", ip: "", node: "nobody"}}; colors.nobody = "#b00"; }}
  colors[row.node] ??= palette[Object.keys(colors).length % palette.length];
  seen.unshift(row); seen.length = Math.min(seen.length, 8);
  document.getElementById("live").innerHTML = seen.map(r =>
    `<div class=row><span class=dot style="background:${{colors[r.node] || "#999"}}"></span><b>${{r.node}}</b> ${{r.pod}} <code>${{r.ip}}</code></div>`).join("");
}}
setInterval(tick, 1000); tick();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, body: str, kind: str = "text/plain") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.reply(200, "ok\n")
        elif self.path == "/ready":
            try:
                self.reply(200 if redis("PING") == "PONG" else 503, "ready\n")
            except (OSError, RuntimeError) as e:
                self.reply(503, f"not ready: {e}\n")
        elif self.path == "/whoami":
            self.reply(200, json.dumps({"pod": POD, "ip": POD_IP, "node": NODE}), "application/json")
        elif self.path == "/work":
            digest = POD.encode()
            for _ in range(200_000):
                digest = hashlib.sha256(digest).digest()
            self.reply(200, f"{POD} {digest.hex()[:12]}\n")
        else:
            self.reply(200, page(), "text/html")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if self.path == "/add" and form.get("item"):
            redis("RPUSH", "list", form["item"][0][:80])
        elif self.path == "/note" and form.get("text"):
            bucket(BUCKET)
            key = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S") + ".txt"
            s3("PUT", f"/{BUCKET}/{key}", body=form["text"][0][:200].encode())
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        if not self.path.startswith(("/healthz", "/ready", "/whoami")):
            print(f"{POD} {self.command} {self.path}", flush=True)


if __name__ == "__main__":
    print(f"shop on :8080, pod {POD} {POD_IP} on {NODE}, redis {REDIS_HOST}, s3 {S3}", flush=True)
    ThreadingHTTPServer(("", 8080), Handler).serve_forever()
