VM ?= cachyos
VIDEO ?=

.PHONY: help list show fetch-iso prep install start boot-check clean clean-all

help:
	@echo "Targets disponibili:"
	@echo "  make list"
	@echo "  make show VM=cachyos"
	@echo "  make fetch-iso VM=cachyos"
	@echo "  make prep VM=cachyos"
	@echo "  make install VM=cachyos"
	@echo "  make start VM=cachyos [VIDEO=safe|std|virtio-gl]"
	@echo "  make boot-check VM=alpine-ci"
	@echo "  make clean VM=cachyos"
	@echo "  make clean-all"
	@echo
	@echo "Variabili:"
	@echo "  VM=$(VM)"
	@echo "  VIDEO=$(VIDEO)"

list:
	./bin/vmctl list

show:
	./bin/vmctl show "$(VM)"

fetch-iso:
	./bin/vmctl fetch-iso "$(VM)"

prep:
	./bin/vmctl prep "$(VM)"

install:
	./bin/vmctl install "$(VM)" $(if $(VIDEO),--video $(VIDEO),)

start:
	./bin/vmctl start "$(VM)" $(if $(VIDEO),--video $(VIDEO),)

boot-check:
	./bin/vmctl boot-check "$(VM)"

clean:
	./bin/vmctl clean "$(VM)"

clean-all:
	./bin/vmctl clean --all
