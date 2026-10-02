#!/usr/bin/env bash
# Helpers shared by the lab tests under vms/labs/<lab>/tests/ (docs/QLAB_IMPORT.md, F2).
#
#   source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
#   assert "sshd is active" on ssh-lab-server systemctl is-active ssh
#   out=$(on ssh-lab-client cat /etc/hostname); assert_contains "hostname" "$out" "ssh-lab-client"
#   report_results "Exercise 1"
#
# `on <vm> <command...>` runs the command in the guest through `vmctl shell <vm> -- ...`: the
# profile's SSH port and key, the user the disk was installed with, BatchMode (a VM that cannot be
# reached fails the check instead of asking for a password), the command's own exit status.
# Every test is re-runnable: it ends with its own cleanup, because check-vms runs them in order.
set -euo pipefail

LAB_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VMCTL="${VMCTL:-$(cd "$LAB_COMMON_DIR/../.." && pwd)/bin/vmctl}"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi
PASS_COUNT=0
FAIL_COUNT=0

log_ok()   { printf '%s  [PASS]%s %s\n' "$GREEN" "$RESET" "$*"; }
log_fail() { printf '%s  [FAIL]%s %s\n' "$RED" "$RESET" "$*"; }
log_info() { printf '%s  [INFO]%s %s\n' "$YELLOW" "$RESET" "$*"; }

on() {
    local vm="$1"; shift
    "$VMCTL" shell "$vm" -- "$@"
}

# assert <description> <command...>: passes when the command exits 0 (its output is discarded).
assert() {
    local description="$1"; shift
    if "$@" >/dev/null 2>&1; then
        log_ok "$description"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        log_fail "$description"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# assert_fail <description> <command...>: passes when the command does NOT exit 0.
assert_fail() {
    local description="$1"; shift
    if "$@" >/dev/null 2>&1; then
        log_fail "$description (succeeded, expected a failure)"; FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        log_ok "$description"; PASS_COUNT=$((PASS_COUNT + 1))
    fi
}

# assert_contains <description> <text> <extended regex>
assert_contains() {
    local description="$1" text="$2" pattern="$3"
    if printf '%s\n' "$text" | grep -qE -- "$pattern"; then
        log_ok "$description"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        log_fail "$description (expected: $pattern)"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

assert_not_contains() {
    local description="$1" text="$2" pattern="$3"
    if printf '%s\n' "$text" | grep -qE -- "$pattern"; then
        log_fail "$description (unexpected: $pattern)"; FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        log_ok "$description"; PASS_COUNT=$((PASS_COUNT + 1))
    fi
}

# report_results [title]: the summary line; the exit status is the number of failed checks.
report_results() {
    local title="${1:-Test}"
    echo ""
    if [[ "$FAIL_COUNT" -eq 0 ]]; then
        printf '%s%s  %s: all %d checks passed%s\n' "$GREEN" "$BOLD" "$title" "$PASS_COUNT" "$RESET"
    else
        printf '%s%s  %s: %d passed, %d failed%s\n' "$RED" "$BOLD" "$title" "$PASS_COUNT" "$FAIL_COUNT" "$RESET"
    fi
    return "$FAIL_COUNT"
}
