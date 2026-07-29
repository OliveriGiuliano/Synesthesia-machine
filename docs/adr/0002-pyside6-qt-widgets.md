# ADR-0002: Use PySide6 and Qt Widgets

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Synesthesia Machine needs a native Windows shell and a scalable custom node canvas.

## Decision

Use PySide6/Qt 6, `QMainWindow`, Qt Widgets, and Graphics View. Do not use Electron, QML, or a
browser canvas for version 1.

## Consequences

Qt objects remain in the UI process. Engine and node algorithm modules may not import Qt widgets.
Release packaging will first use the standalone `pyside6-deploy` path.