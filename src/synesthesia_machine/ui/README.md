# UI package

PySide6/Qt Widgets graph editing, document commands, inspectors, previews, transport, profiling,
settings, themes, and translations.

## Ownership and seams

- `DocumentSession` owns the mutable authoring document and undo stack. Widgets project its immutable
  view model and never mutate `GraphDocument` directly.
- `DocumentSession.changed` covers every persisted edit for scene synchronization and autosave;
  `runtimeChanged` is reserved for edits that require a new engine plan or demand-root set. Moving
  nodes and editing groups/comments must not replace the active runtime generation.
- `MainWindow` composes panels and coordinates the `EngineClient`; slow engine calls use its engine
  executor.
- `DocumentLifecycleController` owns the document lifecycle behaviour off any window: the
  unsaved-changes replacement decision, open/save against the persistence facade, autosave
  recovery offers, and the recent-file list. `MainWindow` is its host, supplying dialogs, the
  recent menu, and status messages, so each behaviour is testable offscreen against a scripted
  host.
- `AutosaveController` owns the whole autosave behaviour of the watched session: the debounce
  trigger that re-arms on every session change, the immediate pre-replacement save, and the
  recovery writes coalesced on a separate serial executor. The window supplies the delay
  (read live from preferences) through a provider and may inject the clock, so the trigger
  policy is testable offscreen without a real timer.
- Device selectors are metadata-driven through `ParameterSpec.device_kind`. The UI consumes the
  engine catalogue and persists IDs; it never imports hardware backends.
- Image algorithms, media decoding, MIDI/audio I/O, full-resolution arrays, and scheduler work do not
  belong here.

## Change checklist

Update both canvas and inspector projections, undo behavior, accessible names/tooltips, and French
translations. Start with `tests/ui/`; cross-process UI behavior also has coverage in
`tests/integration/` as routed by the testing playbook.
