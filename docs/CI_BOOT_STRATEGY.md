# CI Boot Strategy

This document explains why the project uses a dedicated minimal guest profile for CI and what kind of end-to-end validation is realistic on GitHub Actions.

## Goal

The goal is to have a real VM smoke test in CI, not just unit tests around command construction.

A useful CI boot test should:

- download a real ISO;
- prepare a real disk image;
- boot a real guest with QEMU;
- observe guest output on the serial console;
- succeed only if the guest actually reaches a known boot milestone.

## Why A Dedicated CI Guest Exists

The main `cachyos` profile is a real desktop-oriented guest. It is the right profile for local usage, but it is not a good fit for lightweight CI smoke tests because:

- the ISO is large;
- graphical boot is harder to validate in automation;
- GitHub-hosted runners should not be treated as reliable KVM hosts for guest testing;
- full installation flows are much slower and more fragile in software emulation.

For that reason the project also defines `alpine-ci`, a small guest profile meant specifically for CI.

## Why `alpine-ci` Uses Alpine `virt`

The selected ISO is:

- `alpine-virt-3.23.4-x86_64.iso`

Reasoning:

- it is small, around 67 MiB in the current latest stable release index;
- it is designed for virtualized environments;
- Alpine documents that the `virt` ISO already comes configured for serial console use in QEMU;
- this makes it much easier to validate a real boot from CI logs.

By contrast:

- `Tiny Core` is smaller, but less predictable for an automated serial-console smoke test;
- larger desktop ISOs make CI slower and more failure-prone;
- full GUI boot validation is possible, but not the best first step for a stable workflow.

## Why CI Uses TCG Instead Of KVM

The `boot-check` flow uses QEMU with `accel=tcg`.

This is intentional.

GitHub-hosted runners are themselves virtual machines. Depending on nested hardware acceleration for guest VMs is not a robust baseline for CI. A smoke test built on `tcg` is slower, but far more portable and predictable.

The project therefore uses two different expectations:

- local usage can target `kvm` where available;
- CI boot smoke tests should assume `tcg`.

## What `boot-check` Actually Verifies

The command:

```bash
./bin/vmctl boot-check alpine-ci
```

does the following:

1. ensures the ISO exists, downloading it if necessary;
2. ensures the VM artifact directories exist;
3. builds a headless QEMU command with serial output attached to stdio;
4. boots the guest from CD-ROM;
5. watches the serial stream until it finds the expected text;
6. terminates QEMU and reports success.

For `alpine-ci`, the expected text is currently:

- `login:`

That makes the smoke test stronger than a dry-run because the guest must really boot far enough to expose a login prompt on the serial console.

## What This CI Test Does Not Prove

This is a real VM boot test, but it is still a smoke test.

It does not prove:

- full interactive installation success;
- persistence after reboot from installed disk;
- desktop environment usability;
- GPU, audio, or input behavior;
- EFI-specific boot correctness for the main guest.

Those are higher-level validations and should be added incrementally if needed.

## Practical Next Steps

Reasonable future expansions are:

- add a second check that boots from disk after a minimal unattended install;
- add a BIOS-specific example profile besides `alpine-ci`;
- add an EFI-oriented CI guest when a reliable serial-visible boot path is available;
- split CI so unit tests and VM smoke tests can be triggered independently.

## Sources

These choices were based on the following current references checked on April 22, 2026:

- Alpine serial console notes for QEMU and the `virt` image:
  https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot
- Alpine QEMU usage documentation:
  https://wiki.alpinelinux.org/wiki/Qemu
- Alpine latest stable release index with `alpine-virt-3.23.4-x86_64.iso`:
  https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/
- GitHub-hosted runner reference:
  https://docs.github.com/actions/reference/runners/github-hosted-runners

Inference from those sources:

- Alpine `virt` is a better first CI guest than a desktop ISO;
- serial-console boot detection is a realistic smoke-test strategy on GitHub-hosted runners;
- software emulation is the safer default assumption for CI guest boot tests.
