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
  executor. Recovery writes are owned by `AutosaveController`, which coalesces immutable snapshots on
  a separate serial executor.
- Device selectors are metadata-driven through `ParameterSpec.device_kind`. The UI consumes the
  engine catalogue and persists IDs; it never imports hardware backends.
- Image algorithms, media decoding, MIDI/audio I/O, full-resolution arrays, and scheduler work do not
  belong here.

## Change checklist

Update both canvas and inspector projections, undo behavior, accessible names/tooltips, and French
translations. Start with `tests/ui/`; cross-process UI behavior also has coverage in
`tests/integration/` as routed by the testing playbook.
