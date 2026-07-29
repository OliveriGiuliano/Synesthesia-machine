# Phase 7 — Editing productivity and robustness

## Objective

Turn the proven instrument into a dependable daily-use technical application.

## Scope

Groups/comments, alignment/tidy tools, autosave/recovery, backups, missing-media relink, migrations, recent files/settings, improved errors/help, transport selection behaviour, and large-graph UI refinements.

## Persistence rules

- `.synmachine.json`, UTF-8, schema versioned.
- Atomic temporary-write then replace; retain one backup.
- Autosave dirty documents after 60 seconds of inactivity in Local AppData.
- Recovery offered only when newer than explicit save.
- Relative media paths preferred when inside document tree; preserve absolute fallback/fingerprint.
- Clipboard fragments versioned and UUID-remapped.
- Schema and node migrations are pure sequential transformations.

## Ordered tasks

1. comments/groups and command semantics;
2. alignment, distribute, tidy selection;
3. recent files and application settings;
4. atomic save/backup hardening;
5. autosave/recovery and forced-crash test harness;
6. missing-media locate/relink workflow;
7. migration fixtures and validator tool;
8. inline help/error UX and source selection transport;
9. large-graph profiling and LOD/culling refinements.

## Required tests

Exact undo/redo for every new command; crash recovery; backup behaviour; path relocation; migration chain; corrupted/unknown graph errors; large generated graph interaction smoke test; settings round trip.

## Exit criteria

Unsaved work survives a forced crash; older fixture graphs migrate; missing media can be relinked without editing JSON; editing a realistically large graph remains responsive.

## Completion report

Implemented; persistence changes; tests/results; manual UX checks; limitations; deviations; next work.
