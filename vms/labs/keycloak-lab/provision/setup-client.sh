#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
id oidc-app >/dev/null 2>&1 || sudo useradd --system --home /opt/keycloak-lab --shell /usr/sbin/nologin oidc-app
sudo mkdir -p /opt/keycloak-lab
sudo install -m 644 app.py /opt/keycloak-lab/app.py
sudo tee /etc/systemd/system/oidc-app.service >/dev/null <<'UNIT'
[Unit]
Description=Keycloak lab Python OIDC app
After=network-online.target
Wants=network-online.target
[Service]
User=oidc-app
WorkingDirectory=/opt/keycloak-lab
# One worker shares the in-memory session store with backchannel logout requests.
ExecStart=/usr/bin/gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8000 app:app
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable oidc-app
sudo systemctl restart oidc-app
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/ >/dev/null 2>&1; then exit 0; fi
    sleep 1
done
exit 1
