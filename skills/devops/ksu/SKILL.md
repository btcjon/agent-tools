---
name: ksu
description: "Keep Shit Updated (KSU): enroll Mac, Linux/VPS, Windows, and sandbox hosts in quiet nightly software maintenance, audit coverage, schedule updates, diagnose failures, and manage restart preferences."
---

# KSU — Keep Shit Updated

Maintain each enrolled machine at 03:00 in its local timezone. Use Topgrade as the update engine and the bundled Python runner for native scheduling, overlap prevention, bounded recovery, and private receipts. The agent performs enrollment and diagnosis; nightly jobs do not require an agent subscription or an open chat.

Read [setup and operation](references/setup.md) before enrollment. This is a portable public package: discover the user's actual machines and accounts; never assume hostnames, paths, SSH aliases, credentials, or administrative permission. An installed skill does not discover or enroll inaccessible machines automatically.

## Enrollment outcome

For each requested host, establish identity, OS, existing update schedulers, installed package managers, GUI apps, agent installations, and unmanaged software. Verify official updater support and the selected manager's preview. Keep existing pins, custom branches, repositories, and project dependency locks intact. Add supported steps to the host's `only` list; document every omission and its remedy in `coverage.md`. Avoid overlapping another updater for the same packages.

Offer setup choices: time (03:00 local default), app/service restarts (allowed default), full reboot (deferred default), exclusions, and notification policy (quiet default). Record provider-specific restart behavior: allowing restarts does not prove an app was restarted. Automatic reboot is opt-in and occurs only after a successful update and a recognized OS reboot-needed signal. Unknown reboot state stays pending; document detection limits. For strict no-restart operation, exclude any updater that cannot honor that requirement.

Complete `setup`, `preview`, coverage review, a bounded first update and readback, then `schedule`. Set `coverage_reviewed: true` only after the coverage artifact is complete. Show exact enrolled hosts, scheduled time, active scheduler, successful run or remaining failures, supported coverage, and reboot policy. Distinguish offline/unreachable hosts and user-session-only coverage from always-on operation.

## Nightly behavior and repair

Run approved steps noninteractively. A process lock prevents duplicate runs. Save results locally and rotate logs. Transient network/lock failures receive up to two retries; permissions, authentication, and unclassified failures stop that run and remain in the receipt. No ordinary notifications. The independent hourly watchdog writes stale/never-run/failure health locally without waking the user.

When asked to diagnose, read the receipt and exact failed step. Confirm installed state before replay. Repair an expired metadata cache, reachable transport, or configuration issue only with a supported narrow method; rerun the affected step and verify versions plus service/app health. Do not remove package locks, reset source branches, delete environments, disable signature checks, escalate privileges, or force package removals as generic recovery. Record the attempt and stop after two unsuccessful repairs. A quiet failure is visible in status; it is never reported as fully updated.

## Coverage boundaries

Topgrade's installed-platform adapters cover many OS/package/application ecosystems, not literally all software. Firmware, containers, VM guests, source checkouts, project virtual environments, pinned packages, proprietary updaters, and custom agent builds need explicit coverage decisions. Enroll persistent guests as independent hosts; provision ephemeral sandboxes at creation and update their base image through its existing release process.

Keep executables, config, schedules, locks, logs, and credentials host-local. Share only this secret-free skill source. Public installation instructions are in the setup reference; no private infrastructure is required.
