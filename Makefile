# Developer shortcuts for qemu-iso-lab.
#
# Day-to-day VM work goes through the CLI, not through make:
#     vmctl --help            (./bin/vmctl --help before `make install-cli`)
#     vmtui                   dialog-based menu over the same commands

.DEFAULT_GOAL := help
PREFIX ?= $(HOME)/.local
BIN := $(abspath bin)

.PHONY: help setup install-cli uninstall-cli test lint check ci tui init-local-profile

help: ## Show this help
	@printf "\033[1mqemu-iso-lab: developer targets\033[0m\n\n"
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@printf "\nVM lifecycle (list, provision, install, start, shell, bootstrap-*, clean...):\n"
	@printf "  \033[1mvmctl --help\033[0m   or   \033[1mvmtui\033[0m\n"

setup: ## Check host prerequisites (qemu, qemu-img, OVMF, dialog)
	@./bin/vmctl setup

install-cli: ## Symlink vmctl and vmtui into $(PREFIX)/bin (default ~/.local/bin)
	@mkdir -p "$(PREFIX)/bin"
	@ln -sfn "$(BIN)/vmctl" "$(PREFIX)/bin/vmctl"
	@ln -sfn "$(BIN)/vmtui" "$(PREFIX)/bin/vmtui"
	@printf "  linked %s/bin/vmctl and vmtui -> %s\n" "$(PREFIX)" "$(BIN)"
	@case ":$$PATH:" in *":$(PREFIX)/bin:"*) ;; *) printf "  note: %s/bin is not in your PATH\n" "$(PREFIX)";; esac

uninstall-cli: ## Remove the symlinks created by install-cli
	@rm -f "$(PREFIX)/bin/vmctl" "$(PREFIX)/bin/vmtui"

test: ## Run the unit tests (pytest)
	@python3 -m pytest -q tests/

lint: ## Type-check the vmctl package (mypy --strict)
	@python3 -m mypy vmctl/ --strict

check: lint test ## lint + test, run this before pushing

ci: ## Unit tests exactly as GitHub Actions runs them
	@python3 -m unittest discover -s tests -v

tui: ## Open the text UI (same as running vmtui)
	@./bin/vmtui

init-local-profile: ## Create vms/profiles/local.json from the example
	@if [ -e vms/profiles/local.json ]; then \
		printf "  [warn] vms/profiles/local.json already exists\n"; \
	else \
		cp vms/profiles/local.json.example vms/profiles/local.json; \
		printf "  [ok] created vms/profiles/local.json from the template\n"; \
		printf "  edit YOUR_USER, the password/hash and the SSH/dotfile paths before using the *-local profiles\n"; \
	fi
