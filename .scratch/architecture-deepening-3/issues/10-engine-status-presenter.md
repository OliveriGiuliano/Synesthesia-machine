# 10: Headless engine-status presenter

**What to build:** The bridge publishes a normalized `EngineBridgeState`, but `MainWindow` keeps the interpretation: a ~360-line engine-state presentation policy — 15 cache/signature fields in `__init__` (failure signature, four rendered/applied identity fields, three error-signature tuples, two revision-keyed caches) plus 7 `QTimer`s — spread across `_on_engine_state` (a 7-way object-identity fan-out), `_apply_engine_telemetry` (error digests, tooltips, status summary strings), `_show_engine_failure` (crash dialog with its own signature dedup), `_on_engine_status_message` (8 message keys mapped to status text), and `_refresh_engine_status` (the known-stopped + selection-skip rule). The policy is testable only through a full `MainWindow` with an in-process client: `tests/ui/test_process_supervision.py` drives `window._refresh_engine_status()` and asserts `window._engine_status` / `window._engine_tasks_inflight` with `# pyright: ignore[reportPrivateUsage]` and `processEvents()` busy-waits. Everything below the bridge's `on_state` seam is locked behind the 2130-line class, even though the bridge itself is pure and headlessly testable.

**Solution:** Extract an `EngineStatusPresenter` in the same shape as the `PreviewRouter` and `DocumentLifecycleController` extractions: `EngineBridgeState` in, display intents out (status-bar text, telemetry rendering, crash/failure dialog with its dedup, profiler/profile application, status-message mapping), holding the signature and identity caches it needs. `MainWindow` keeps rendering published intents and forwarding user commands; it keeps no engine-state interpretation of its own.

**Status:** resolved

**Blocked by:** 01

**Files:**
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/engine_bridge.py` (state contract only, if fields are missing)
- `src/synesthesia_machine/ui/engine_status_presenter.py` (new)
- `tests/ui/test_process_supervision.py`
- `tests/ui/test_engine_bridge.py`

**Acceptance:**
- [x] The presenter is Qt-free: `EngineBridgeState` in, display intents out, testable without a window
- [x] `MainWindow` holds no engine-state signature/identity cache
- [x] Crash-dialog dedup, status rendering, and telemetry application are asserted headlessly against the presenter
- [x] Full-window tests still pass with the presenter behind the window's handlers
- [x] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (report candidate "Finish the window split"; UI-lifecycle scout F1). Blocked by 01 because the presenter's input contract is the bridge's published state, which the driver-surface ticket settles.
- 2026-09-19: Resolved — `EngineStatusPresenter` owns the failure/identity caches and the intent mappings (telemetry, device catalogue, profiles, activation/transport/restart, status messages); `MainWindow` renders the intents and localizes the engine-provided error text. Two-axis review passed; its findings (window-localized empty-error fallbacks, frozen `StateIntents`, device/transport identity-dedup tests) were addressed. Headless presenter suite plus full suite green.
