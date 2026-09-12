# Possible future improvements

Reviewed on 2026-09-12 against v0.1.5. This is an implementation proposal;
none of the phases below is recorded as complete.

The project already has strict mypy checks, a substantial test suite, real guest
boot tests in CI, packaging and bilingual documentation. Three improvements would
make configuration errors easier to diagnose and ongoing maintenance easier:

1. Add a profile schema and strict runtime validation.
2. Split lifecycle responsibilities, then consolidate duplicated bootstrap code.
3. Adopt consistent linting, formatting and checks locally and in CI.

Implement these as independently reviewable and integrable changes in that order.
Phase 2 has two separate steps; formatting also gets a dedicated commit. Do not
require one long-lived branch containing all phases before anything can land.
Estimate tooling work after inspecting existing violations, rather than assuming
it takes half a day.

## Shared constraints

- Read the repository instructions and `CLAUDE.md` architecture and verified-live
  notes before implementation. Reconcile stale documentation with the code and
  applicable instructions; this proposal does not override `AGENTS.md`.
- Preserve plain QEMU with optional libvirt export, distro-specific rendering,
  and mutable state accessed through `state.X`.
- Preserve existing behavior for valid profiles: commands, QEMU arguments, logs,
  artifact paths, exit codes and installation sequencing. Phase 1 deliberately
  changes acceptance and diagnostics for invalid configuration and adds `validate`.
- Keep imports acyclic. Update architecture documentation when module boundaries
  change. Refresh obsolete Makefile examples without rewriting live-test history
  or the physical-disk flash contract. Development tooling does not itself require
  relaxing the stdlib-only runtime constraint.
- Keep unit tests isolated from host tools, real ISOs, installed guests and the
  personal `vms/profiles/local.json`. Use temporary roots and mocked processes.
- Do not alter personal overrides, existing VM artifacts, cached ISOs or running
  VMs during refactoring. Use disposable roots and guests for integration checks.
- Run `make check` for each implementation step and the corresponding local
  unittest targets for bootstrap or CI changes. Run the full unittest suite before
  final delivery while unittest compatibility is part of the project contract.
- Capture before/after dry-run output in an isolated scratch checkout for
  `bootstrap-preseed debian-server`, `bootstrap-kickstart almalinux-server`,
  `bootstrap-archinstall arch-noctalia`, `bootstrap-alpine alpine-niri`,
  `bootstrap-autoyast opensuse-tumbleweed-autoyast`,
  `bootstrap-unattended ubuntu-niri`, `install-unattended ubuntu-niri` and
  `post-install ubuntu-niri`. Preserve the same environment and input state;
  explain any variable path or timestamp normalization used for comparison.
- Dry runs do not establish live installation success. For changed bootstrap
  paths, run representative disposable guest installations and disk boots before
  claiming live verification. CI changes also require the local `alpine-ci` smoke
  flow. Report unavailable checks explicitly; never advance `meta.verified` from
  unit tests or dry runs.
- Keep commits focused and use short imperative subjects. Do not push as part of
  implementing this proposal unless separately requested.

## Phase 1 — profile schema and strict validation

`validate_vm_profile()` currently accepts unknown keys and validates only part of
the model. Catch misspellings such as `memory_mbb` with a profile name, JSON path
and actionable error. Schema validation improves runtime safety; it does not
replace `dict[str, Any]` with static types. A typed internal model is separate work.

Deliverables:

1. Define schemas for profile documents, complete VM definitions and partial
   local overrides. Files contain a `vms` mapping, sometimes with multiple VMs;
   they are not bare VM definitions. Allow document metadata such as `$schema`
   and the existing `_comment`. Keep partial overrides valid in editors without
   requiring fields supplied by the base profile; validate complete definitions
   after merge and preserve aliases, list concatenation and `{{user}}` expansion.
2. Describe every supported field by checking tracked profiles and configuration
   consumers in `vmctl/`. Close fixed-shape objects to unknown keys. Preserve
   dynamic maps such as VM names and `video.variants`, validating their values
   without restricting names to today's examples. Use enums only for genuinely
   closed sets and document the properties.
3. Put schemas outside the directory scanned for VM definitions, or explicitly
   exclude them in every profile-discovery consumer. Today `load_config()` reads
   all `vms/profiles/*.json` and requires a `vms` object: simply adding
   `vms/profiles/schema.json` would break loading. Add appropriate relative
   `$schema` references to tracked documents and `local.json.example`.
4. Record the validation-engine decision before implementing it. Under the
   current stdlib-only constraint, keep any custom evaluator small, specify its
   supported schema subset and reject unsupported validation keywords explicitly.
   Account for dynamic maps, schema references and conditional requirements before
   claiming that a short keyword list is sufficient. If using a third-party
   runtime validator instead, first resolve that dependency-policy change.
   Exercise schema conformance against a reference validator in development
   tests; it must not silently become an untested optional path in CI.
5. Add `vmctl validate [name...]` and register it in `COMMAND_GROUPS`. With no
   names, validate all profiles. Return 0 and `N profiles valid` on success;
   return 1 and list errors on stderr on failure. With `--json`, emit only an
   array of `{profile, path, message}` errors on stdout, including `[]` on success.
   Separate loading from error presentation so invalid input can reach this
   command's structured diagnostics. Define diagnostics for malformed files,
   missing names and cross-profile conflicts as well as individual fields.
6. Retain semantic checks that the schema does not express, including real dates,
   video-variant references, identity consistency and SSH-port conflicts. Never
   weaken existing validation while replacing structural checks.
7. Test valid tracked documents, partial overrides, merged profiles, unknown keys,
   nested paths, dynamic map entries, schema-file discovery and CLI exit/output
   behavior. If implementing an evaluator, test its supported keywords and
   agreement with the reference validator, including boolean versus integer types.
   Document the command and schema in `docs/PROFILES.md` and the README.

Acceptance: all tracked profiles validate in isolation; a temporary misspelled
key produces an error identifying it; valid local merges still work; existing
`vmctl list --json` output is unchanged. The schema is available wherever the
supported installation methods need it.

## Phase 2a — split lifecycle without changing behavior

Move cohesive groups out of `vmctl/lifecycle.py` before changing their control
flow. Candidate boundaries are runtime/start/stop/console, inventory,
maintenance, local validation, network labs, provisioning and bootstrap commands.
Determine the actual split from dependencies, including install commands, guest
agent commands and libvirt operations. Do not enforce arbitrary line counts or
leave handlers without a clear owner.

If introducing `vmctl/lifecycle/`, retain the imports used by CLI and other callers
through explicit re-exports. Update `tests/_common.py` and direct mock targets:
re-exporting a function does not make patches to its former module affect globals
in its new defining module. Keep runtime helpers below orchestration in the import
graph, and verify fresh imports do not depend on accidental initialization order.

Acceptance: function moves are reviewable separately from logic changes, existing
tests pass, and before/after dry runs preserve behavior.

## Phase 2b — consolidate bootstrap behavior incrementally

There is substantial duplication, especially between preseed and kickstart, but
the six Linux handlers do not implement one identical serial-token protocol:

- Debian renders preseed into the initrd during boot-artifact preparation; it does
  not attach the same kind of separate seed media as kickstart.
- Kickstart has an ostree-reference preparation step for Silverblue.
- Arch and Alpine have live-console automation; AutoYaST has its own media layout.
- Ubuntu's `cmd_bootstrap_unattended()` delegates to `cmd_install_unattended()`,
  which currently uses `runtime.run()` to wait for QEMU, not `run_and_expect()`.
- Windows and pfSense retain their separate installation and shutdown semantics.

Start with the common sequence shared by a small number of handlers. Extract
preparation, launch or post-install helpers where their behavior actually matches,
then migrate other compatible paths in separate commits. Introduce a flow
dataclass only if the resulting call sites justify it; do not prescribe a large
callback interface or require each distro module to import orchestration just to
publish a `FLOW` constant. Shared types must not create dependency cycles.

Preserve each path's preparation order, media, console mode, timeouts, completion
criterion and background launch. In particular, do not convert Ubuntu to a token
flow as part of a behavior-preserving refactor. Start and post-install may be shared
only where their arguments and behavior match.

For token-based flows, preserve guest sync + flush -> completion token -> natural
poweroff. Keep host token handling and the existing wait/fallback policy centralized
in `qemu.run_and_expect()`. Do not introduce a second implementation of process
termination or change its timing.

Acceptance:

- Test each migrated path's actual preparation and call order, not a universal
  seed-before-extraction assumption.
- Verify timeout, failure-token and launch failures prevent subsequent startup or
  post-install as appropriate. Cover background-launch and post-install failures
  without weakening cleanup or error reporting.
- Preserve Ubuntu's process-exit path and Windows/pfSense behavior in regression
  coverage even when their handlers only move modules.
- Pass unit checks, before/after dry-run comparisons and representative live
  installation/disk-boot checks for the paths changed. Report the tested profiles
  and remaining live coverage explicitly.

## Phase 3 — tooling consistency

1. Inventory existing lint violations and choose an explicit Python 3.10-compatible
   Ruff configuration and file scope. Start with manageable rules; assess
   `E,F,I,UP,B` before enabling them all. Review automatic fixes for behavior changes.
   Keep bulk formatting in its own commit regardless of diff size.
2. Configure local and CI checks consistently: lint, format verification, strict
   mypy and the test suite. Keep `make lint`, `make check` and `make ci` aligned with
   their documented meanings. A switch from unittest to pytest is optional;
   consistency of coverage and checks is the actual goal.
3. Add a development extra for required tools, including pre-commit if it is the
   documented installation route. Configure pre-commit with explicit versions and
   ensure its mypy environment and invocation cover the intended package. Use
   compatible tool versions locally, in hooks and in CI.
4. Document setup in `docs/DEVELOPMENT.md`. Preserve guest smoke/install and dry-run
   jobs while updating the unit-test job. Include local checks and the Alpine
   smoke result in the delivery report when the workflow changes.

Acceptance: lint, format checks, `pre-commit run --all-files` and `make check` pass
with the documented development environment; `make ci` reproduces the intended CI
checks. Formatting is independently reviewable from logic and configuration.

## Delivery report for each implementation step

- Scope, branch and commit subjects.
- Validation commands and results, including live profiles exercised and checks
  that could not be run.
- Before/after dry-run differences and any justified normalization.
- Compatibility changes, unresolved decisions and work intentionally left for
  later steps.

Update this document's completion status only as work is actually delivered.
