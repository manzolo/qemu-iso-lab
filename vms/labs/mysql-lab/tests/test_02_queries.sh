#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 2 — SQL queries${RESET}"; echo ""
assert_contains "four users" "$(sql 'SELECT COUNT(*) FROM users;')" "^4$"
assert_contains "WHERE LIKE filters" "$(sql "SELECT name FROM users WHERE name LIKE 'A%';")" "^Alice$"
assert_contains "ORDER BY puts the laptop first" "$(sql 'SELECT product FROM orders ORDER BY amount DESC LIMIT 1;')" "^Laptop$"
assert_contains "JOIN matches the four orders to their users" "$(sql 'SELECT COUNT(*) FROM users u JOIN orders o ON o.user_id = u.id;')" "^4$"
assert_contains "LEFT JOIN keeps Dana, who has no order" "$(sql 'SELECT u.name FROM users u LEFT JOIN orders o ON o.user_id = u.id WHERE o.id IS NULL;')" "^Dana$"
assert_contains "GROUP BY sums Alice's orders" "$(sql "SELECT SUM(o.amount) FROM users u JOIN orders o ON o.user_id = u.id WHERE u.name = 'Alice' GROUP BY u.id;")" "^1079\.98$"
report_results "Exercise 2"
