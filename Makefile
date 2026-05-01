VM ?= cachyos
VIDEO ?=
ESC := \033
ifneq ($(strip $(NO_COLOR)),1)
ifneq ($(strip $(TERM)),)
ifneq ($(strip $(TERM)),dumb)
BOLD := $(ESC)[1m
BLUE := $(ESC)[34m
CYAN := $(ESC)[36m
GREEN := $(ESC)[32m
YELLOW := $(ESC)[33m
RESET := $(ESC)[0m
endif
endif
endif

.PHONY: help setup list status show fetch-iso prep install install-unattended start boot-check tui clean clean-all init-local-profile

define print_header
	@printf "$(BOLD)$(BLUE)==>$(RESET) $(BOLD)%s$(RESET)\n" "$(1)"
endef

define print_kv
	@printf "  $(CYAN)%-8s$(RESET) %s\n" "$(1)" "$(2)"
endef

help:
	$(call print_header,Available targets)
	$(call print_kv,make,setup)
	$(call print_kv,make,list)
	$(call print_kv,make,status)
	$(call print_kv,make,show VM=cachyos)
	$(call print_kv,make,fetch-iso VM=cachyos)
	$(call print_kv,make,prep VM=cachyos)
	$(call print_kv,make,install VM=cachyos)
	$(call print_kv,make,install-unattended VM=ubuntu-niri-local [VIDEO=safe|std|virtio-gl])
	$(call print_kv,make,start VM=cachyos [VIDEO=safe|std|virtio-gl])
	$(call print_kv,make,boot-check VM=alpine-ci)
	$(call print_kv,make,init-local-profile)
	$(call print_kv,make,tui)
	$(call print_kv,make,clean VM=cachyos)
	$(call print_kv,make,clean-all)
	@printf "\n"
	$(call print_header,Variables)
	$(call print_kv,VM,$(VM))
	$(call print_kv,VIDEO,$(if $(VIDEO),$(VIDEO),<default>))

setup:
	@./bin/vmctl setup

list:
	$(call print_header,Configured VMs)
	@./bin/vmctl list

status:
	$(call print_header,VM status)
	@./bin/vmctl status

show:
	$(call print_header,VM profile)
	$(call print_kv,VM,$(VM))
	@./bin/vmctl show "$(VM)"

fetch-iso:
	$(call print_header,Download ISO)
	$(call print_kv,VM,$(VM))
	@./bin/vmctl fetch-iso "$(VM)"

prep:
	$(call print_header,Prepare VM)
	$(call print_kv,VM,$(VM))
	@./bin/vmctl prep "$(VM)"

install:
	$(call print_header,Boot installer)
	$(call print_kv,VM,$(VM))
	$(call print_kv,VIDEO,$(if $(VIDEO),$(VIDEO),<default>))
	@./bin/vmctl install "$(VM)" $(if $(VIDEO),--video $(VIDEO),)

install-unattended:
	$(call print_header,Boot unattended installer)
	$(call print_kv,VM,$(VM))
	$(call print_kv,VIDEO,$(if $(VIDEO),$(VIDEO),<default>))
	@./bin/vmctl install-unattended "$(VM)" $(if $(VIDEO),--video $(VIDEO),)

start:
	$(call print_header,Start VM)
	$(call print_kv,VM,$(VM))
	$(call print_kv,VIDEO,$(if $(VIDEO),$(VIDEO),<default>))
	@./bin/vmctl start "$(VM)" $(if $(VIDEO),--video $(VIDEO),)

boot-check:
	$(call print_header,Boot smoke check)
	$(call print_kv,VM,$(VM))
	@./bin/vmctl boot-check "$(VM)"

init-local-profile:
	$(call print_header,Initialize local profile)
	@if [ -e vms/profiles/local.json ]; then \
		printf "  $(YELLOW)[warn]$(RESET) vms/profiles/local.json already exists\n"; \
	else \
		cp vms/profiles/local.json.example vms/profiles/local.json; \
		printf "  $(GREEN)[ok]$(RESET) Created vms/profiles/local.json from the template\n"; \
		printf "  $(CYAN)note$(RESET) Edit YOUR_USER and the SSH/dotfiles paths before using ubuntu-niri-local\n"; \
	fi

tui:
	$(call print_header,Open TUI)
	@./bin/vmtui

clean:
	$(call print_header,Clean VM artifacts)
	$(call print_kv,VM,$(VM))
	@./bin/vmctl clean "$(VM)"

clean-all:
	$(call print_header,Clean artifacts of all VMs)
	@./bin/vmctl clean --all
