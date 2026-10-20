#!/usr/bin/env bash
# Runs inside the server guest, as the profile user. Re-running preserves the realm.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=26.4.7
SHA256=aca60ab72ae4d67adc6b8fe7fca5e08d16a6bb5e4a4f2ebb54050e3f5503fd2b
# Digest from the official GitHub release asset API, recorded 2026-10-20.
if [ ! -x "/opt/keycloak-$VERSION/bin/kc.sh" ]; then
    curl -fL --retry 4 "https://github.com/keycloak/keycloak/releases/download/$VERSION/keycloak-$VERSION.tar.gz" -o /tmp/keycloak.tar.gz
    echo "$SHA256  /tmp/keycloak.tar.gz" | sha256sum -c -
    sudo tar -xzf /tmp/keycloak.tar.gz -C /opt
    rm /tmp/keycloak.tar.gz
fi
id keycloak >/dev/null 2>&1 || sudo useradd --system --home /opt/keycloak --shell /usr/sbin/nologin keycloak
sudo ln -sfn "/opt/keycloak-$VERSION" /opt/keycloak
sudo systemctl enable --now postgresql
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='keycloak'" | grep -qx 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE USER keycloak WITH PASSWORD 'db-lab-pass';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='keycloak'" | grep -qx 1; then
    sudo -u postgres createdb --owner=keycloak keycloak
fi
sudo mkdir -p /opt/keycloak/data/import
sudo cp realm.json /opt/keycloak/data/import/lab-realm.json
sudo tee /opt/keycloak/conf/keycloak.conf >/dev/null <<'CONF'
db=postgres
db-url=jdbc:postgresql://127.0.0.1:5432/keycloak
db-username=keycloak
db-password=db-lab-pass
hostname=http://127.0.0.1:18080
http-enabled=true
http-port=8080
health-enabled=true
CONF
sudo chown -R keycloak:keycloak "/opt/keycloak-$VERSION"
sudo chmod 640 /opt/keycloak/conf/keycloak.conf
sudo tee /etc/systemd/system/keycloak.service >/dev/null <<'UNIT'
[Unit]
Description=Keycloak 26.4.7 learning lab (HTTP, development mode)
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service
[Service]
User=keycloak
Group=keycloak
Environment=KC_BOOTSTRAP_ADMIN_USERNAME=admin
Environment=KC_BOOTSTRAP_ADMIN_PASSWORD=admin-lab-pass
Environment="JAVA_OPTS_KC_HEAP=-Xms256m -Xmx768m"
ExecStart=/opt/keycloak/bin/kc.sh start-dev --import-realm
Restart=on-failure
RestartSec=5
TimeoutStartSec=240
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
UNIT
sudo install -m 755 kc-lab /usr/local/bin/kc-lab
sudo systemctl daemon-reload
sudo systemctl enable --now keycloak
for i in $(seq 1 120); do
    if curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1; then
        exit 0
    fi
    sleep 2
done
sudo journalctl -u keycloak -n 80 --no-pager
exit 1
