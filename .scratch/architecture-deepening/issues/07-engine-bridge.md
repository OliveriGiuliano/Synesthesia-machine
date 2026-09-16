# 07: Extract the EngineBridge

**What to build:** Everything the editor does to drive the engine — preview pumping, transport commands, activation scheduling/debounce, and status/telemetry handling — lives in one deep module behind a small interface, so the main window is a thin compositor that renders state and forwards user intents. A maintainer changes engine-orchestration behaviour (activation debouncing, the preview pump) in one place, and it is testable without a `QMainWindow` or time-based waits.

**Blocked by:** 01 (Extract the demand-root policy headless)

**Status:** done — `EngineBridge` + Qt adapters landed; `MainWindow` is a thin compositor over the bridge; 11 headless bridge tests + full suite + offscreen smoke green

- [x] An EngineBridge module owns the engine client, the preview pump (and its per-port cursors), transport, activation scheduling/debounce, and status/telemetry handling.
- [x] The main window no longer holds these responsibilities directly; it renders and forwards intents.
- [x] Engine-orchestration behaviour (activation, transport, preview pump) is testable headlessly with a fake engine client, without offscreen windows or `qWait`-style timing.
- [x] Observable editor behaviour is unchanged; existing UI/runtime tests pass.
