# Phase 2 — Minimal node editor and document commands

## Objective

Create a functional visual editor for the Phase 1 graph model. The editor must manipulate domain commands rather than own a second graph representation.

## Out of scope

Real video, camera, MIDI, image algorithms, engine process, elaborate animation, minimap, groups, and final polish.

## Fixed UI structure

- QMainWindow with File/Edit/View/Graph menus.
- left searchable node library;
- central QGraphicsView/QGraphicsScene canvas;
- right inspector;
- status bar;
- custom node/port/cable graphics items;
- theme tokens; no hard-coded widget colours.

## Allowed paths

`ui/`, `app/`, `persistence/clipboard.py`, `persistence/autosave.py` skeleton, `ui/commands/`, UI tests, and narrowly required graph facades. Do not put algorithms in `ui/`.

## Required behaviour

- add nodes by library drag and graph search;
- select, marquee-select, move, delete, duplicate;
- pan/zoom/frame selection;
- create and replace typed connections;
- compatible target highlighting;
- dropping a cable on empty canvas opens a compatible-node search;
- render connectable parameter socket and literal editor on the same row;
- connected value overrides literal while preserving fallback;
- undo/redo through QUndoStack commands;
- copy/paste remaps UUIDs;
- save/load and dirty-state indication.

## Architecture constraints

- Scene items observe NodeViewModels; they do not directly mutate GraphDocument.
- All mutations are command objects.
- Node positions are domain/document values; temporary drag state is view-only until command commit.
- Type resolution and validation come from Phase 1, not UI reimplementation.
- Repaints are event-driven and throttled.

## Ordered tasks

1. Main window shell, action registry, settings, theme tokens.
2. GraphView/GraphScene and coordinate/zoom behaviour.
3. NodeGraphicsItem, PortGraphicsItem, ConnectionGraphicsItem.
4. GraphDocument adapter/view models and command dispatcher.
5. Node library/search and creation workflows.
6. Parameter editor factory, including connectable parameters.
7. Undo/redo, clipboard, save/load, recent file skeleton.
8. Error/warning badges using ValidationReport.
9. Keyboard shortcuts and high-DPI tests.

## Required tests

- command redo/undo for every mutation;
- connection replacement as one undoable command;
- incompatible connection refusal;
- paste UUID remap and internal-edge preservation;
- save/load from UI preserves positions/parameters;
- no duplicate model after repeated undo/redo;
- Qt smoke tests at offscreen platform where possible.

## Exit criteria

A user can create, edit, save, reopen, copy, and undo a utility-only graph. The graph domain remains usable with no Qt application running.

## Completion report

Implemented; interfaces; tests/results; limitations; screenshots/manual checks; deviations; next work.
