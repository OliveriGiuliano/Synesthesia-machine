# ADR 0010: Engine-owned device catalogues and stable selections

- Status: Accepted
- Date: 2026-08-20

## Context

Camera, MIDI, and audio devices are opened in the engine process, but the editor previously exposed
their node parameters as free-text fields. The UI could not enumerate devices without crossing the
process boundary, MIDI display aliases were not reliable identities, and users had to know backend
syntax such as `opencv:0`.

## Decision

The engine process owns device discovery. `EngineClient.device_catalogue()` returns a versioned IPC
value containing a stable device ID, a separate display name, and the device kind. Discovery may
return a partial catalogue with pending kinds or a per-kind error; failure to enumerate one backend
does not hide the others. Camera probing remains asynchronous to the engine command loop, and the UI
polls only while a kind is pending.

Node definitions declare device intent with `ParameterSpec.device_kind`. The editor uses this
metadata to render labelled selectors while persisting only the device ID. A selected ID that is no
longer enumerated remains visible as unavailable instead of being silently replaced. The editor
performs one automatic discovery pass and also offers an explicit refresh action; enumeration is
dispatched through the UI's existing background engine task path.

Camera IDs retain the existing `opencv:<index>` compatibility contract. New MIDI selections use the
raw backend name as identity and a normalized friendly label for display. New audio selections use
`sounddevice:<index>`; the empty audio ID remains the system default. Existing saved MIDI friendly
names and audio device names continue to be accepted when opening a device.

## Consequences

- Qt and the UI process never import or call camera, RtMidi, PortAudio, or sounddevice discovery.
- The engine protocol advances from version 13 to 14.
- Graph schema and node implementation versions do not change because the persisted fields remain
  strings and legacy string values remain valid.
- Backend IDs are more reliable than labels but can still become unavailable after operating-system
  device changes; the UI reports that state explicitly and never auto-selects another output.
- Adding another device-backed parameter requires only a device kind, engine enumerator, and tests;
  it does not require a bespoke Qt widget.

## References

- `docs/adr/0004-midi-stack.md`
- `docs/adr/0005-ui-engine-process-separation.md`
- `docs/architecture/master.md`
