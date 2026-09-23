# Developer shortcuts for qemu-iso-lab.
#
# Day-to-day VM work goes through the CLI, not through make:
#     vmctl --help            (./bin/vmctl --help before `make install-cli`)
#     vmtui                   text UI (fzf, or dialog) over the same commands

.DEFAULT_GOAL := help
PREFIX ?= $(HOME)/.local
# check-vms defaults to 300 s per phase, enough only for boot checks: a real install takes 10-60 min.
TIMEOUT ?= 3600
BIN := $(abspath bin)
TUI_PYTHON ?= $(if $(wildcard .venv-tui/bin/python),.venv-tui/bin/python,python3)

.PHONY: help setup install-cli uninstall-cli test lint check ci tui tui-preview init-local-profile validate-vms groups guides

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

tui-preview: ## Try the experimental Textual dashboard (optional tui dependency)
	@$(TUI_PYTHON) ./bin/vmtui-preview

init-local-profile: ## Create vms/profiles/local.json from the example
	@if [ -e vms/profiles/local.json ]; then \
		printf "  [warn] vms/profiles/local.json already exists\n"; \
	else \
		cp vms/profiles/local.json.example vms/profiles/local.json; \
		printf "  [ok] created vms/profiles/local.json from the template\n"; \
		printf "  edit YOUR_USER, the password/hash and the SSH/dotfile paths before using personal profile overrides\n"; \
	fi

validate-vms: ## Local-only matrix: reinstall every unattended profile from scratch (experimental ones are reported as skipped), restore the installed disks, write the HTML report (hours; GROUP="ubuntu rhel" runs one category, VMS="a b" names profiles, PARALLEL=auto (default) packs VMs by free RAM/CPUs or PARALLEL=N fixes the count, TIMEOUT=3600 s per phase)
	@./bin/vmctl check-vms $(VMS) $(foreach group,$(GROUP),--group $(group)) --restore --no-clean-first --report --parallel $(or $(PARALLEL),auto) --timeout $(TIMEOUT) --open

groups: ## List the profile categories check-vms --group accepts (also: vmctl list --groups)
	@./bin/vmctl list --groups

guides: ## Render docs/guides/{it,en} into docs/guides/pdf/<lang>/ (one manual + single PDFs; needs python markdown + weasyprint)
	python3 tools/build_guides.py
