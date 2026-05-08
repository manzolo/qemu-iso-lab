VM ?= cachyos
VIDEO ?=
TIMEOUT ?= 300
CLEAN_STALE_VM ?=
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

.PHONY: help setup list status show fetch-iso prep install install-unattended start boot-check check-vms clean-stale tui clean clean-all init-local-profile

define print_header
	@printf "$(BOLD)$(BLUE)==>$(RESET) $(BOLD)%s$(RESET)\n" "$(1)"
endef

define print_kv
	@printf "  $(CYAN)%-8s$(RESET) %s\n" "$(1)" "$(2)"
endef

help:
	$(call print_header,Available targets)
	$(call print_kv,setup,Check host dependencies)
	$(call print_kv,list,List configured VM profiles)
	$(call print_kv,status,Show local VM artifact/runtime status)
	$(call print_kv,show,Show one resolved VM profile)
	$(call print_kv,fetch-iso,Download or validate one VM ISO)
	$(call print_kv,prep,Prepare one VM disk/NVRAM)
	$(call print_kv,install,Boot one installer)
	$(call print_kv,install-unattended,Run Ubuntu autoinstall for one VM)
	$(call print_kv,start,Start an installed VM)
	$(call print_kv,boot-check,Run serial boot smoke check for one VM)
	$(call print_kv,check-vms,Run the local VM validation matrix)
	$(call print_kv,clean-stale,Remove stale runtime state)
	$(call print_kv,init-local-profile,Create vms/profiles/local.json)
	$(call print_kv,tui,Open the text UI)
	$(call print_kv,clean,Remove artifacts for one VM)
	$(call print_kv,clean-all,Remove artifacts for all VMs)
	@printf "\n"
	$(call print_header,Variables)
	$(call print_kv,VM,$(VM))
	$(call print_kv,VIDEO,$(if $(VIDEO),$(VIDEO),<default>))
	$(call print_kv,TIMEOUT,$(TIMEOUT))
	$(call print_kv,VMS,$(if $(VMS),$(VMS),<all>))
	$(call print_kv,CLEAN_STALE_VM,$(if $(CLEAN_STALE_VM),$(CLEAN_STALE_VM),<all>))
	@printf "\n"
	$(call print_header,Examples)
	$(call print_kv,show,make show VM=ubuntu-niri)
	$(call print_kv,install,make install VM=cachyos VIDEO=virtio-gl)
	$(call print_kv,boot-check,make boot-check VM=alpine-ci)
	$(call print_kv,check-vms,make check-vms)
	$(call print_kv,clean-stale,make clean-stale)
	$(call print_kv,subset,make check-vms VMS=ubuntu-niri TIMEOUT=600)
	$(call print_kv,parallel,make check-vms PARALLEL=3)
	$(call print_kv,note,VMS accepts space-separated profile names)

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

check-vms:
	$(call print_header,Local VM test matrix)
	$(call print_kv,VMS,$(if $(VMS),$(VMS),<all>))
	$(call print_kv,TIMEOUT,$(TIMEOUT))
	$(call print_kv,PARALLEL,$(PARALLEL))
	$(call print_kv,CLEAN_FIRST,$(CLEAN_FIRST))
	@./bin/vmctl check-vms --timeout $(TIMEOUT) --parallel $(if $(PARALLEL),$(PARALLEL),1) $(if $(CLEAN_FIRST),--clean-first,) $(VMS)

clean-stale:
	$(call print_header,Clean stale runtime state)
	$(call print_kv,VM,$(if $(CLEAN_STALE_VM),$(CLEAN_STALE_VM),<all>))
	@./bin/vmctl clean-stale $(CLEAN_STALE_VM)

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
