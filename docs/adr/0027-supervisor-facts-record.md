# ADR 0027: Supervisor facts as a typed record outside the engine telemetry

- Status: Accepted
- Date: 2026-09-19
- Context: ADR-0005 (UI/engine process separation), ADR-0026 (one engine body,
  two placements), protocol v17

## Context

`EngineMetrics` mixed engine telemetry (state, ticks, fps, latency percentiles,
mailbox state, runtime errors) with process-supervisor facts (`child_process_id`,
`restart_count`, `heartbeat_age_s`, `uptime_s`, `cpu_percent`,
`system_memory_bytes`). The mixed record was assembled by a three-layer
`dataclasses.replace` chain across the process seam: the engine body filled the
telemetry, the child server replaced four fields with the child's own
measurements, and the parent client replaced two more with its supervision
facts — or synthesized a `state=ERROR` record from supervisor facts alone when
the child had crashed. Over the in-process placement the supervisor fields were
permanently at defaults, so `child_process_id=None + heartbeat_age_s=0.0`
meant two different things (no child process exists, versus the child is dead)
depending on the placement, and a consumer could read `cpu_percent` as
meaningful in a placement where it is never measured.

## Decision

- **The supervisor facts are their own typed record.** `EngineSupervisorFacts`
  (in `contracts/engine_client.py`) holds the six supervisor fields;
  `EngineMetrics` keeps only the engine telemetry plus a
  `supervisor: EngineSupervisorFacts` field. A consumer cannot mistake a
  vacuous in-process default for a measurement because the record's docstring
  states the per-placement meaning: in-process placements always carry the
  vacuous default (no child process exists), while the process placement's
  child fills `child_process_id`, `uptime_s`, `cpu_percent` and
  `system_memory_bytes` and the parent client fills `restart_count` and
  `heartbeat_age_s`.
- **Each layer fills only the record it owns.** The engine body reports
  telemetry with the default supervisor record; the child server's metrics
  reply replaces the whole sub-record with the child's own measurements; the
  parent client layers its supervision facts onto the sub-record on the way
  out, and synthesizes the full record from supervisor facts when the child
  is crashed or unresponsive.
- **The wire change is an explicit protocol bump.** The nested record changes
  the pickled `MetricsResponse` payload, so the engine protocol moves from
  v17 to v18. Parent and child always ship in one build, so the handshake
  version guard — not a payload migration — is the compatibility mechanism,
  and the protocol-version consistency test pins the constant.
- `EngineStatus` keeps its own `child_process_id`/`restart_count` fields:
  that record is the connection state (parent-owned), a different
  responsibility from the metrics telemetry.

## Consequences

- The in-process placement's `child_process_id=None + heartbeat_age_s=0.0`
  now means exactly one documented thing (no child process), and a test pins
  the vacuous in-process shape and the synthesized crashed-child shape.
- A new placement or a new supervision fact gains a named field on
  `EngineSupervisorFacts` instead of another ad-hoc `replace` target on a
  mixed record.
- The diagnostic bundle's `engine_metrics.json` nests the supervisor facts
  under a `supervisor` key; the bundle is on-demand output, so no migration
  is required.

## References

- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/contracts/engine_messages.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/ui/main_window.py`
- `docs/adr/0026-one-engine-body-two-placements.md`
