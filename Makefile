# Developer shortcuts for qemu-iso-lab.
#
# Day-to-day VM work goes through the CLI, not through make:
#     vmctl --help            (./bin/vmctl --help before `make install-cli`)
#     vmtui                   text UI (Textual dashboard; fzf/dialog with --classic)

.DEFAULT_GOAL := help
PREFIX ?= $(HOME)/.local
# check-vms defaults to 300 s per phase, enough only for boot checks: a real install takes 10-60 min.
TIMEOUT ?= 3600
BIN := $(abspath bin)

.PHONY: help setup install-cli uninstall-cli test lint check ci tui tui-classic init-local-profile validate-vms groups guides

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

lint: ## Type-check the vmctl package (mypy --strict; the Textual modules with .venv-tui when present)
	@python3 -m mypy vmctl/ --strict --exclude 'vmctl/tui_(textual|widgets)\.py$$'
	@if [ -x .venv-tui/bin/python ]; then \
		python3 -m mypy vmctl/tui_bridge.py vmctl/tui_textual.py vmctl/tui_widgets.py --strict \
			--follow-imports=silent --python-executable=.venv-tui/bin/python; \
	else printf '  skipped the Textual modules (no .venv-tui)\n'; fi

check: lint test ## lint + test, run this before pushing

ci: ## Unit tests exactly as GitHub Actions runs them
	@python3 -m unittest discover -s tests -v

tui: ## Open the text UI (same as running vmtui: Textual when installed, else fzf/dialog)
	@./bin/vmtui

tui-classic: ## Open the classic fzf/dialog menus
	@./bin/vmtui --classic


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
