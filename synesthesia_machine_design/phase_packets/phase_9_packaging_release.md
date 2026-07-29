# Phase 9 — Packaging and release hardening

## Objective

Produce a reproducible Windows 11 x64 application that runs without a Python development environment.

## Fixed packaging decision

Use `pyside6-deploy`/Nuitka in standalone-directory mode first. Do not start with one-file mode. Build on Windows from the committed lockfile.

## Deliverables

- finalized `pysidedeploy.spec`;
- application/version resources and icon;
- deterministic build script;
- dependency inventory and bundled licence notices;
- settings/log/autosave/crash locations documented;
- clean-machine smoke-test PowerShell script;
- optional installer wrapper only after standalone is proven;
- release checklist, versioning/tagging, rollback instructions;
- signed-build plan if code signing is later available.

## Required smoke matrix

On a clean supported Windows 11 x64 VM/user account:

- launch/close/relaunch;
- create/save/open graph;
- decode common H.264/MP4 test video;
- camera enumeration and one capture when hardware is available;
- MIDI port enumeration and mock/loopback send;
- debug audio;
- engine crash/restart;
- autosave recovery;
- diagnostic export;
- uninstall/delete leaves user documents intact.

## Licensing checkpoint

Before external distribution, review Qt LGPL obligations, the exact FFmpeg/PyAV build configuration and codecs, RtMidi/Mido, OpenCV, sounddevice/PortAudio, and all transitive licences. Record the chosen application licence and distribution model. This is a release gate and requires competent legal review when commercial/proprietary distribution is intended.

## Exit criteria

A clean machine runs the app without Python. Core media/MIDI/audio functions operate from packaged paths. Build provenance and dependency versions are reproducible. No debug console or development-only files leak into the release unless intentionally included.

## Completion report

Build command/artifacts; smoke results; licences/notices; known packaging limitations; release checklist status; deviations.
