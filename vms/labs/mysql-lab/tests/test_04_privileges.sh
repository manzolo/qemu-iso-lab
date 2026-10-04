#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 4 — users and privileges${RESET}"; echo ""
cleanup() { rootsql "DROP USER IF EXISTS 'reader'@'localhost';" >/dev/null; }
trap cleanup EXIT
cleanup
# reader's statements, on stdin as well; the password is the exercise's own.
reader() { printf '%s\n' "$1" | on "$V" mysql -u reader -pReader123! -N -B testdb 2>&1 || true; }
rootsql "CREATE USER 'reader'@'localhost' IDENTIFIED BY 'Reader123!'; GRANT SELECT ON testdb.* TO 'reader'@'localhost';" >/dev/null
assert_contains "SHOW GRANTS lists SELECT on testdb" "$(rootsql "SHOW GRANTS FOR 'reader'@'localhost';")" "GRANT SELECT ON .testdb"
assert_contains "reader may SELECT" "$(reader 'SELECT COUNT(*) FROM users;')" "^4$"
assert_contains "reader may not INSERT" "$(reader "INSERT INTO users (name) VALUES ('x');")" "denied"
rootsql "REVOKE SELECT ON testdb.* FROM 'reader'@'localhost';" >/dev/null
assert_contains "after REVOKE reader is denied" "$(reader 'SELECT 1 FROM users;')" "denied"
rootsql "DROP USER 'reader'@'localhost';" >/dev/null
assert_contains "DROP USER removes the account" "$(rootsql "SELECT COUNT(*) FROM mysql.user WHERE user = 'reader';")" "^0$"
report_results "Exercise 4"
