#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 3 — changing data, in transactions${RESET}"; echo ""
cleanup() { sql "DELETE FROM users WHERE name IN ('Eve', 'Ghost', 'Frank');" >/dev/null; }
trap cleanup EXIT
cleanup
sql "INSERT INTO users (name, email) VALUES ('Eve', 'eve@example.com');" >/dev/null
assert_contains "INSERT adds Eve" "$(sql "SELECT COUNT(*) FROM users WHERE name = 'Eve';")" "^1$"
sql "UPDATE users SET email = 'eve@lab.local' WHERE name = 'Eve';" >/dev/null
assert_contains "UPDATE changes her email" "$(sql "SELECT email FROM users WHERE name = 'Eve';")" "^eve@lab\.local$"
sql "DELETE FROM users WHERE name = 'Eve';" >/dev/null
assert_contains "DELETE removes her" "$(sql "SELECT COUNT(*) FROM users WHERE name = 'Eve';")" "^0$"
assert_contains "inside the transaction the new row is there" "$(sql "START TRANSACTION; INSERT INTO users (name) VALUES ('Ghost'); SELECT COUNT(*) FROM users WHERE name = 'Ghost'; ROLLBACK;")" "^1$"
assert_contains "ROLLBACK takes it back" "$(sql "SELECT COUNT(*) FROM users WHERE name = 'Ghost';")" "^0$"
sql "START TRANSACTION; INSERT INTO users (name) VALUES ('Frank'); COMMIT;" >/dev/null
assert_contains "COMMIT keeps it" "$(sql "SELECT COUNT(*) FROM users WHERE name = 'Frank';")" "^1$"
assert_contains "the foreign key refuses to delete Alice" "$(sql "DELETE FROM users WHERE name = 'Alice';")" "foreign key constraint fails"
report_results "Exercise 3"
