#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 6 — security and configuration${RESET}"; echo ""
assert_contains "bind-address is 0.0.0.0" "$(on "$V" "grep -E '^bind-address' /etc/mysql/mysql.conf.d/mysqld.cnf" || true)" "0\.0\.0\.0"
assert_contains "MySQL listens on every address, port 3306" "$(on "$V" ss -ltn || true)" "0\.0\.0\.0:3306"
assert_contains "root authenticates by socket" "$(rootsql "SELECT plugin FROM mysql.user WHERE user = 'root';")" "auth_socket"
assert_contains "labuser authenticates by password" "$(rootsql "SELECT plugin FROM mysql.user WHERE user = 'labuser' AND host = 'localhost';")" "caching_sha2_password|mysql_native_password"
assert_contains "phpMyAdmin answers inside the VM" "$(on "$V" curl -fs http://127.0.0.1/phpmyadmin/ || true)" "phpMyAdmin"
assert_contains "phpMyAdmin answers from the host on 8088" "$(curl -fs --max-time 10 http://127.0.0.1:8088/phpmyadmin/ || true)" "phpMyAdmin"
assert "the reset script runs" on "$V" 'bash ~/setup-mysql-lab.sh'
assert_contains "after the reset testdb is as the lab hands it" "$(sql 'SELECT COUNT(*) FROM users;')" "^4$"
report_results "Exercise 6"
