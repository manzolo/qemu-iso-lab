"""The Kubernetes course's backup: the Redis snapshot an init container saved in /backup goes to
the bucket "backups" on RustFS, signed by the app's own S3 code (app.py, same ConfigMap)."""
import datetime
import sys

sys.path.insert(0, "/app")
import app  # noqa: E402

app.bucket("backups")
key = datetime.datetime.now(datetime.timezone.utc).strftime("redis-%Y%m%d-%H%M%S.rdb")
with open("/backup/dump.rdb", "rb") as f:
    data = f.read()
app.s3("PUT", f"/backups/{key}", body=data)
print(f"backups/{key}: {len(data)} bytes")
