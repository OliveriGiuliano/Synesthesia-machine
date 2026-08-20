# Qt editor playbook

- User-visible graph mutations go through `DocumentSession` and focused `QUndoCommand` classes. Do not
  mutate the document directly from widgets in a way that bypasses undo/redo, dirty state, autosave,
  validation, scene synchronization, or runtime activation.
- Keep domain logic in graph/persistence/runtime services and projection logic in view models. Widgets
  should not become a second source of graph truth.
- Preserve incremental scene synchronization, lazy parameter editors, low-zoom detail suppression,
  and stable graphics-item identity; these are measured large-graph behaviors.
- Stable graphics-item identity also means transient rendered payloads survive ordinary scene sync.
  Explicitly clear or replace runtime-only images, text, badges, errors, and sequence cursors at the
  lifecycle boundary that invalidates them.
- Use the central action registry for commands and shortcuts. Give interactive controls useful object
  names, accessible names, status tips, and tooltips where neighboring code does so.
- All authored visible English strings must pass through `ui.translations.tr()` or `trf()`, have a
  French entry when the feature is user-visible, and refresh through the relevant `retranslate()`
  path after a language switch.
- Qt tests must create one application, process deferred deletion, close test-owned top-level widgets,
  and inject temporary `QSettings` and `ApplicationPaths`. Do not write tests against the developer's
  real registry settings, logs, recovery directory, clipboard contents, or home directory.
- Set `QT_QPA_PLATFORM=offscreen` before importing PySide6 in headless test modules.
- Integration tests must assert both positive and negative routing: the intended widget changes and an
  unrelated retained widget or connection does not change.
