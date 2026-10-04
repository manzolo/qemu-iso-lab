#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mysql.sh"
echo ""; echo "${BOLD}Exercise 5 — administration: indexes, backup, restore${RESET}"; echo ""
cleanup() { rootsql 'DROP DATABASE IF EXISTS testlab;' >/dev/null; on "$V" 'rm -f ~/testlab.sql' >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
rootsql "CREATE DATABASE testlab; GRANT ALL ON testlab.* TO 'labuser'@'localhost';" >/dev/null
sql "CREATE TABLE students (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), grade INT); INSERT INTO students (name, grade) VALUES ('Ann', 28), ('Ben', 24), ('Cleo', 30);" testlab >/dev/null
assert_contains "the table holds three students" "$(sql 'SELECT COUNT(*) FROM students;' testlab)" "^3$"
sql 'ALTER TABLE students ADD COLUMN email VARCHAR(100);' testlab >/dev/null
assert_contains "ALTER TABLE added a column" "$(sql 'SHOW COLUMNS FROM students;' testlab)" "email"
assert_contains "without an index EXPLAIN reads every row" "$(sql 'EXPLAIN SELECT * FROM students WHERE grade = 30;' testlab)" "ALL"
sql 'CREATE INDEX idx_grade ON students (grade);' testlab >/dev/null
assert_contains "with the index EXPLAIN uses idx_grade" "$(sql 'EXPLAIN SELECT * FROM students WHERE grade = 30;' testlab)" "idx_grade"
assert "mysqldump writes the backup" on "$V" 'mysqldump --no-tablespaces testlab > ~/testlab.sql && grep -q INSERT ~/testlab.sql'
sql 'DROP TABLE students;' testlab >/dev/null
assert "the restore reads it back" on "$V" 'mysql testlab < ~/testlab.sql'
assert_contains "the students are back" "$(sql 'SELECT COUNT(*) FROM students;' testlab)" "^3$"
report_results "Exercise 5"
