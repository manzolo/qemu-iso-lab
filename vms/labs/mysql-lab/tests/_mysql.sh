#!/usr/bin/env bash
# Shared by the mysql-lab tests. The SQL travels on stdin, never inside a command line: no quoting
# to get through the host shell, ssh and the guest shell. Output is -N -B (no headers, tabs).
V=mysql-lab-server
# sql "<statements>" [database]: as labuser (~/.my.cnf in the guest), testdb by default.
sql() { printf '%s\n' "$1" | on "$V" mysql -N -B "${2:-testdb}" 2>&1 || true; }
# rootsql "<statements>": as root, over the Unix socket (auth_socket).
rootsql() { printf '%s\n' "$1" | on "$V" sudo mysql -N -B 2>&1 || true; }
# `vmctl group test` may start the VM: SSH answers before mysqld has finished starting, and the
# first test met it "activating" (2026-10-04). Every test waits for it, up to 90 s.
for _ in $(seq 1 45); do
    [ "$(on "$V" systemctl is-active mysql 2>/dev/null || true)" = "active" ] && break
    sleep 2
done
