#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 1 — MySQL anatomy${RESET}"; echo ""
assert_contains "the mysql service is active" "$(on $V systemctl is-active mysql || true)" "^active$"
assert_contains "the client answers with its version" "$(on $V mysql --version || true)" "Ver 8\."
assert_contains "root reaches the server over the socket" "$(rootsql 'SHOW DATABASES;')" "testdb"
assert_contains "testdb has an orders table" "$(sql 'SHOW TABLES;')" "orders"
assert_contains "labuser logs in from ~/.my.cnf" "$(sql 'SELECT CURRENT_USER();')" "labuser@localhost"
assert_contains "the data directory holds testdb" "$(on $V sudo ls /var/lib/mysql || true)" "testdb"
report_results "Exercise 1"
